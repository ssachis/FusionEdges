"""Provider-agnostic chat with tool use, plus automatic fallback between providers.

Conversation format used everywhere else in FusionEdges (so a run can switch
provider mid-conversation):

    {"role": "user", "text": "..."}
    {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "args"}]}
    {"role": "tool", "results": [{"id", "name", "content"}]}

Providers are tried in the order of FUSIONEDGES_PROVIDERS (default
"groq,gemini,anthropic,openai"); a provider is used only if its API key is set.
If a call fails with a rate limit, quota, auth, size or network error, the
provider is put on cooldown and the same call is retried on the next one.
Every call is recorded (provider, model, tokens, seconds, fallbacks).

Stdlib only. Keys are read from the environment and never logged.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

UA = "fusionedges/0.3 (+https://github.com)"  # some gateways reject urllib's default UA


@dataclass
class ProviderSpec:
    name: str
    kind: str              # "anthropic" or "openai" (OpenAI-compatible chat completions)
    url: str
    key_env: str
    default_model: str
    price: tuple           # rough USD per 1M (input, output); override with FUSIONEDGES_PRICE_<NAME>="in,out"


SPECS = {
    "groq": ProviderSpec("groq", "openai", "https://api.groq.com/openai/v1/chat/completions",
                         "GROQ_API_KEY", "openai/gpt-oss-120b", (0.15, 0.60)),
    "gemini": ProviderSpec("gemini", "openai",
                           "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                           "GEMINI_API_KEY", "gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite", (0.30, 2.50)),
    # NVIDIA build.nvidia.com: free developer key, no card, OpenAI-compatible. Model ids change; set
    # FUSIONEDGES_NVIDIA_MODEL to any id from the catalog (e.g. a DeepSeek or Nemotron model).
    "nvidia": ProviderSpec("nvidia", "openai", "https://integrate.api.nvidia.com/v1/chat/completions",
                           "NVIDIA_API_KEY", "meta/llama-3.3-70b-instruct", (0.0, 0.0)),
    "anthropic": ProviderSpec("anthropic", "anthropic", "https://api.anthropic.com/v1/messages",
                              "ANTHROPIC_API_KEY", "claude-sonnet-5-5", (3.0, 15.0)),
    "openai": ProviderSpec("openai", "openai", "https://api.openai.com/v1/chat/completions",
                           "OPENAI_API_KEY", "gpt-4.1-mini", (0.40, 1.60)),
    "openrouter": ProviderSpec("openrouter", "openai", "https://openrouter.ai/api/v1/chat/completions",
                               "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct", (0.13, 0.40)),
}
DEFAULT_ORDER = "groq,gemini,nvidia,anthropic,openai,openrouter"

# Test hook: name -> callable(system, messages, tools, max_tokens) -> (assistant_msg, usage dict)
CUSTOM: dict = {}

_COOLDOWN: dict = {}   # provider name -> unix time until which it is skipped


class LLMError(RuntimeError):
    pass


class _Retryable(Exception):
    """This provider failed in a way another provider may not (limits, auth, outage)."""

    def __init__(self, msg, cooldown):
        super().__init__(msg)
        self.cooldown = cooldown


def model_list(name: str) -> list:
    """Models to try for a provider, best first. FUSIONEDGES_<NAME>_MODEL may hold several, comma-separated;
    when one is busy or retired the next one is used."""
    if name in CUSTOM:
        return [name]
    raw = os.environ.get(f"FUSIONEDGES_{name.upper()}_MODEL") or SPECS[name].default_model
    return [m.strip() for m in raw.split(",") if m.strip()] or [SPECS[name].default_model]


def model_for(name: str, idx: int = 0) -> str:
    ms = model_list(name)
    return ms[min(idx, len(ms) - 1)]


def short_reason(msg: str) -> str:
    """One log-friendly line. Keeps provider size limits ('Limit 8000, Requested 12000') that a plain
    90-character cut hides, and says which limit it was when the provider names it."""
    m = re.search(r"limit\D{0,4}(\d[\d,]*)(?:\D{1,12}used\D{0,4}(\d[\d,]*))?\D{1,12}requested\D{0,4}(\d[\d,]*)", msg, re.I)
    if m:
        which = " per-minute tokens" if re.search(r"tokens per minute|TPM", msg, re.I) else \
            " per-day tokens" if re.search(r"tokens per day|TPD", msg, re.I) else ""
        used = f", already used {m.group(2).rstrip(',')}" if m.group(2) else ""
        again = re.search(r"try again in ([\dhms.]+)", msg, re.I)
        return (f"limit {m.group(1).rstrip(',')}{which}{used}, requested {m.group(3).rstrip(',')}"
                + (f"; provider says retry in {again.group(1)}" if again else "") + f": {msg[:60]}")
    m = re.search(r'"message"\s*:\s*"([^"]{5,400})', msg)
    if m:                       # provider JSON error: show its own sentence, not the first 90 chars of JSON
        return re.sub(r"\s+", " ", msg.split(":", 1)[0] + ": " + m.group(1))[:300]
    return re.sub(r"\s+", " ", msg)[:160]


def max_tokens_for(name: str, requested: int) -> int:
    """Answer-size cap for one provider. Some providers count the requested answer size against a
    per-minute token limit, so a small cap (FUSIONEDGES_GROQ_MAX_TOKENS=4000) can avoid HTTP 413."""
    raw = os.environ.get(f"FUSIONEDGES_{name.upper()}_MAX_TOKENS")
    try:
        return max(256, min(requested, int(raw))) if raw else requested
    except ValueError:
        return requested


def price_for(name: str) -> tuple:
    raw = os.environ.get(f"FUSIONEDGES_PRICE_{name.upper()}")
    if raw:
        try:
            a, b = (float(x) for x in raw.split(","))
            return a, b
        except ValueError:
            pass
    return SPECS[name].price if name in SPECS else (0.0, 0.0)


def configured() -> list:
    """Providers in fallback order that have a key (or are test fakes)."""
    order = [p.strip().lower() for p in os.environ.get("FUSIONEDGES_PROVIDERS", DEFAULT_ORDER).split(",") if p.strip()]
    out = []
    for n in order:
        if n in CUSTOM or (n in SPECS and os.environ.get(SPECS[n].key_env)):
            out.append(n)
    return out


def status() -> list:
    now = time.time()
    order = [p.strip().lower() for p in os.environ.get("FUSIONEDGES_PROVIDERS", DEFAULT_ORDER).split(",") if p.strip()]
    rows = []
    for n in order:
        if n not in SPECS and n not in CUSTOM:
            continue
        has = n in CUSTOM or bool(os.environ.get(SPECS[n].key_env))
        rows.append({"name": n, "model": model_for(n), "has_key": has,
                     "key_env": SPECS[n].key_env if n in SPECS else "",
                     "cooling_down": max(0, round(_COOLDOWN.get(n, 0) - now))})
    return rows


# ------------------------------------------------------------------ HTTP
def _post(url: str, headers: dict, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode()
    h = {"content-type": "application/json", "user-agent": UA, **headers}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:600]
        code = e.code
        retry_after = e.headers.get("retry-after") if e.headers else None
        low = body.lower()
        if code == 429 or "rate limit" in low or "quota" in low or "resource_exhausted" in low:
            wait = 60
            try:
                wait = max(5, min(600, int(float(retry_after)))) if retry_after else 60
            except ValueError:
                pass
            raise _Retryable(f"rate limit / quota (HTTP {code}): {body[:200]}", wait)
        if code in (401, 403):
            raise _Retryable(f"auth or access blocked (HTTP {code}): {body[:200]}", 3600)
        if code in (402, 413) or "context" in low and "length" in low or "too large" in low:
            raise _Retryable(f"request too large or no credit (HTTP {code}): {body[:200]}", 30)
        if code >= 500:
            raise _Retryable(f"provider error (HTTP {code}): {body[:200]}", 30)
        if code == 400 and ("tool" in low or "function" in low or "model" in low):
            # e.g. the model produced a malformed tool call or doesn't support tools: try another model
            raise _Retryable(f"bad request on this model (HTTP 400): {body[:200]}", 10)
        raise LLMError(f"HTTP {code}: {body[:300]}")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise _Retryable(f"network error: {getattr(e, 'reason', e)}", 30)


# ------------------------------------------------------- format adapters
def _safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s or "id")[:64] or "id"


def _anthropic(spec, model, key, system, messages, tools, max_tokens):
    out = []

    def push(role, blocks):
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        if m["role"] == "user":
            push("user", [{"type": "text", "text": m["text"] or "(empty)"}])
        elif m["role"] == "assistant":
            blocks = []
            if (m.get("text") or "").strip():
                blocks.append({"type": "text", "text": m["text"]})
            for c in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": _safe_id(c["id"]), "name": c["name"], "input": c["args"]})
            push("assistant", blocks or [{"type": "text", "text": "(no output)"}])
        elif m["role"] == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": _safe_id(r["id"]), "content": r["content"]}
                          for r in m["results"]])
    payload = {"model": model, "max_tokens": max_tokens, "system": system, "messages": out}
    if tools:
        payload["tools"] = [{"name": t["name"], "description": t["description"],
                             "input_schema": t["input_schema"]} for t in tools]
    resp = _post(spec.url, {"x-api-key": key, "anthropic-version": "2023-06-01"}, payload)
    text, calls = [], []
    for b in resp.get("content", []):
        if b.get("type") == "text":
            text.append(b["text"])
        elif b.get("type") == "tool_use":
            calls.append({"id": b["id"], "name": b["name"], "args": b.get("input") or {}})
    u = resp.get("usage", {})
    return ({"role": "assistant", "text": "\n".join(text), "tool_calls": calls},
            {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0)})


def _openai(spec, model, key, system, messages, tools, max_tokens):
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("text") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"id": _safe_id(c["id"]), "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                     for c in m["tool_calls"]]
            out.append(msg)
        elif m["role"] == "tool":
            for r in m["results"]:
                out.append({"role": "tool", "tool_call_id": _safe_id(r["id"]), "content": r["content"]})
    payload = {"model": model, "messages": out, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
        payload["tool_choice"] = "auto"
    resp = _post(spec.url, {"authorization": f"Bearer {key}"}, payload)
    choice = (resp.get("choices") or [{}])[0].get("message", {}) or {}
    calls = []
    for i, c in enumerate(choice.get("tool_calls") or []):
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
            if not isinstance(args, dict):
                args = {"_raw": args}
        except json.JSONDecodeError:
            args = {"_invalid_json": fn.get("arguments", "")[:500]}
        calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name", ""), "args": args})
    u = resp.get("usage") or {}
    return ({"role": "assistant", "text": choice.get("content") or "", "tool_calls": calls},
            {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0)})


# ------------------------------------------------------------- session
@dataclass
class Session:
    """Tracks every call made for one purpose (report, plain run, guided run)."""
    label: str
    log: object = print
    calls: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    midx: dict = field(default_factory=dict)   # provider -> index of the model currently in use

    def chat(self, system: str, messages: list, tools=None, max_tokens: int = 4096) -> dict:
        providers = configured()
        if not providers:
            raise LLMError("No AI provider key is set. Export at least one of: " +
                           ", ".join(s.key_env for s in SPECS.values()))
        errors = []
        waits = 0
        while True:
            soonest = None
            busy = False
            retry_model = False
            now = time.time()
            ready = [p for p in providers if _COOLDOWN.get(p, 0) <= now] or providers  # all cooling: try anyway
            for name in ready:
                t0 = time.time()
                sent = len(system) + sum(len(m.get("text") or "") + sum(len(r.get("content", "")) for r in m.get("results", []))
                                         for m in messages)
                est = sent / CHARS_PER_TOKEN
                gap = pacing_wait(name, est)
                if gap > 0 and name not in CUSTOM:
                    self.log(f"[{self.label}] pacing: {name} allows {tpm_limit(name):,} tokens a minute and recent "
                             f"calls used most of it; waiting {round(gap)}s before sending")
                    _sleep(min(gap, 90))
                    _USAGE[name] = []
                if not waits:
                    self.log(f"[{self.label}] sending {sent:,} chars (~{round(est):,} tokens, estimated at "
                             f"{CHARS_PER_TOKEN} chars per token) to {name}, answer cap "
                             f"{max_tokens_for(name, max_tokens):,} tokens")
                try:
                    if name in CUSTOM:
                        msg, usage = CUSTOM[name](system, messages, tools or [], max_tokens)
                    else:
                        spec = SPECS[name]
                        fn = _anthropic if spec.kind == "anthropic" else _openai
                        msg, usage = fn(spec, model_for(name, self.midx.get(name, 0)), os.environ[spec.key_env],
                                        system, messages, tools or [], max_tokens_for(name, max_tokens))
                except _Retryable as e:
                    _COOLDOWN[name] = time.time() + e.cooldown
                    errors.append(f"{name}: {e}")
                    if "rate limit" in str(e) and (soonest is None or e.cooldown < soonest):
                        soonest = e.cooldown  # a per-minute limit: waiting helps (a size error would not)
                    if re.search(r"quota|rate limit|429", str(e), re.I) and name not in CUSTOM \
                            and self.midx.get(name, 0) + 1 < len(model_list(name)):
                        # free-tier quotas are per model: the next model in the list has its own allowance
                        old = model_for(name, self.midx.get(name, 0))
                        self.midx[name] = self.midx.get(name, 0) + 1
                        _COOLDOWN.pop(name, None)
                        self.log(f"[{self.label}] {name} model {old} is out of quota ({short_reason(str(e))[:120]}); "
                                 f"trying {model_for(name, self.midx[name])}")
                        retry_model = True
                        break
                    if re.search(r"503|UNAVAILABLE|overloaded|high demand", str(e), re.I):
                        busy = True  # the provider is momentarily full; the same request usually works in seconds
                    self.fallbacks.append({"from": name, "reason": str(e)[:400], "at": round(time.time())})
                    self.log(f"[{self.label}] {name} unavailable ({short_reason(str(e))}); switching provider")
                    continue
                except LLMError as e:
                    if name not in CUSTOM and re.search(r"HTTP 404|no longer available|not found", str(e), re.I) \
                            and self.midx.get(name, 0) + 1 < len(model_list(name)):
                        old = model_for(name, self.midx.get(name, 0))
                        self.midx[name] = self.midx.get(name, 0) + 1
                        self.log(f"[{self.label}] {name} model {old} is not available; trying "
                                 f"{model_for(name, self.midx[name])}")
                        retry_model = True
                        break
                    errors.append(f"{name}: {e}")
                    self.fallbacks.append({"from": name, "reason": str(e)[:200], "at": round(time.time())})
                    self.log(f"[{self.label}] {name} error ({str(e)[:90]}); switching provider")
                    continue
                record_usage(name, usage["in"] + usage["out"])
                pin, pout = price_for(name)
                self.calls.append({"provider": name, "model": model_for(name, self.midx.get(name, 0)),
                                   "in": usage["in"], "out": usage["out"],
                                   "cost": (usage["in"] * pin + usage["out"] * pout) / 1e6,
                                   "seconds": round(time.time() - t0, 2)})
                msg["provider"] = name
                return msg
            if retry_model:
                continue
            if busy and soonest is None and waits >= 2 and self.midx.get(providers[0], 0) + 1 < len(model_list(providers[0])):
                name0 = providers[0]
                old = model_for(name0, self.midx.get(name0, 0))
                self.midx[name0] = self.midx.get(name0, 0) + 1
                waits = 0
                self.log(f"[{self.label}] {old} is still busy; switching to {model_for(name0, self.midx[name0])}")
                _COOLDOWN.pop(name0, None)
                continue
            if busy and soonest is None and waits < 4:
                waits += 1
                pause = 5 * waits
                self.log(f"[{self.label}] the provider is busy (HTTP 503); waiting {pause}s and retrying ({waits}/4)")
                _sleep(pause)
                continue
            limit = wait_max()
            if soonest is not None and soonest <= limit and waits < 2:
                waits += 1
                self.log(f"[{self.label}] every provider is rate-limited; waiting {round(soonest) + 1}s and retrying "
                         f"({waits}/2). The free limits are per minute.")
                _sleep(soonest + 1)
                continue
            raise LLMError("All providers failed. " + " | ".join(short_reason(e) for e in errors))

    def totals(self) -> dict:
        models = sorted({f"{c['provider']}:{c['model']}" for c in self.calls})
        return {"api_calls": len(self.calls),
                "input_tokens": sum(c["in"] for c in self.calls),
                "output_tokens": sum(c["out"] for c in self.calls),
                "est_cost_usd": round(sum(c["cost"] for c in self.calls), 5),
                "models": models, "fallbacks": list(self.fallbacks)}


_sleep = time.sleep  # replaced in tests
_USAGE: dict = {}  # provider -> [(finished_at, tokens)] of recent calls, for per-minute pacing
DEFAULT_TPM = {"groq": 8000}  # free-tier tokens per minute; FUSIONEDGES_<NAME>_TPM overrides, 0 turns pacing off
CHARS_PER_TOKEN = 2.3  # code and JSON on Groq measured about 2.0-2.5 characters per token, not the usual 4


def tpm_limit(name: str) -> int:
    raw = os.environ.get(f"FUSIONEDGES_{name.upper()}_TPM")
    try:
        return int(raw) if raw not in (None, "") else DEFAULT_TPM.get(name, 0)
    except ValueError:
        return DEFAULT_TPM.get(name, 0)


def pacing_wait(name: str, est_tokens: float, now: float | None = None) -> float:
    """Seconds to wait so this request fits in the provider's per-minute allowance (0 = send now)."""
    limit = tpm_limit(name)
    now = time.time() if now is None else now
    if not limit or est_tokens >= limit * 0.9:  # waiting cannot help a request that is itself too big
        return 0.0
    recent = [(ts, n) for ts, n in _USAGE.get(name, []) if now - ts < 60]
    _USAGE[name] = recent
    used = sum(n for _, n in recent)
    if used + est_tokens <= limit * 0.92:
        return 0.0
    over = used + est_tokens - limit * 0.92
    freed = 0
    for ts, n in sorted(recent):
        freed += n
        if freed >= over:
            return max(0.0, ts + 60 - now) + 1
    return 61.0


def record_usage(name: str, tokens: int, now: float | None = None):
    _USAGE.setdefault(name, []).append((time.time() if now is None else now, tokens))


def wait_max() -> float:
    """Longest wait (seconds) worth sitting through when every provider is rate-limited. 0 disables waiting."""
    try:
        return float(os.environ.get("FUSIONEDGES_WAIT_MAX", "75"))
    except ValueError:
        return 75.0


def reset_cooldowns():
    _COOLDOWN.clear()
    _USAGE.clear()
