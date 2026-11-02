"""Reproducible, conservative evidence tools. No AI calls or DB writes on import."""
from __future__ import annotations
import hashlib
import json
import math
import random
import re
from pathlib import Path
from statistics import mean, median


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def repo_key(spec):
    s = str(spec).lower().strip().rstrip('/')
    s = re.sub(r'^https?://(?:www\.)?github.com/', '', s)
    return s.split('/tree/')[0].removesuffix('.git')


def code_hash():
    root = Path(__file__).parent
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.py'))})


def validate_spec(spec, conditions=None, repeats=None, build=True):
    """Strict lane: reject an unpinned, changed or underspecified experiment before spending tokens."""
    from . import llm
    conds = conditions or spec.get('conditions', [])
    tasks = spec.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('Explicit nonempty tasks required')
    if not build or (repeats or spec.get('repeats', 0)) < 2:
        raise ValueError('Evidence runs require builds and at least two repeats')
    if set(conds) - {'prompt', 'port-ai', 'port-report', 'port-report-memory', 'adaptive'} or 'prompt' not in conds:
        raise ValueError('Use prompt, port-ai, port-report, port-report-memory')
    env = spec.get('env', {})
    if env.get('FUSIONEDGES_PROVIDERS') != 'gemini' or not env.get('FUSIONEDGES_GEMINI_MODEL') or ',' in env['FUSIONEDGES_GEMINI_MODEL']:
        raise ValueError('Pin exactly one Gemini model; no provider/model fallback')
    if env.get('FUSIONEDGES_BROWSER') != '1':
        raise ValueError('Browser checks must be enabled')
    keys = []
    for t in tasks:
        for role in ('source', 'donor'):
            if not re.search(r'/tree/[0-9a-f]{40}(?:/|$)', t.get(role, '')):
                raise ValueError('Every repository must use a full immutable commit URL')
        if not t.get('component'):
            raise ValueError('Every task needs a component')
        from .contracts import validate
        validate(t.get('acceptance') or {})
        keys.append((t['source'], t['donor'], t['component']))
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate tasks inflate sample size')
    if spec.get('code_sha256') != code_hash():
        raise ValueError('Code changed: freeze a new experiment before running')
    if 'port-report-memory' in conds:
        p = Path(env.get('FUSIONEDGES_MEMORY_SNAPSHOT', ''))
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != spec.get('memory_sha256'):
            raise ValueError('Frozen memory missing or changed')
    from . import browser
    ok, why = browser.available()
    if not ok:
        raise ValueError('Browser unavailable: ' + str(why))
    if not llm.configured():
        raise ValueError('No Gemini key in this process. Launch from the terminal with GEMINI_API_KEY configured.')
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser_instance = pw.chromium.launch()
            browser_instance.close()
    except Exception as exc:
        raise ValueError('Chromium must actually launch before an evidence experiment') from exc


def strict_success(rec):
    if rec.get('mode') == 'adaptive' and rec.get('routing', {}).get('status') != 'automated-pass':
        return False
    checks = (rec.get('checks') or {}).get('checks') or {}
    applicable = [v.get('pass') for v in checks.values() if v.get('pass') is not None]
    browser = rec.get('browser') or {}
    agent = rec.get('agent') or {}
    return (rec.get('status') == 'done' and (rec.get('build') or {}).get('ok') is True
            and bool(applicable) and all(x is True for x in applicable)
            and browser.get('pass') is True and not browser.get('skipped')
            and not agent.get('error') and not agent.get('repair_error'))


def summarize(rows, seed=261102):
    """Rows are paired task/repeat/condition observations; failures stay in cost denominators.

    CI resamples tasks (not repetitions). This does not establish repo-independent generalization.
    """
    groups = {}
    for x in rows:
        groups.setdefault(x['condition'], []).append(x)
    out = {}
    for cond, xs in groups.items():
        succeeded = sum(x['success'] for x in xs)
        tokens = sum(x['tokens'] for x in xs)
        seconds = sum(x['seconds'] for x in xs)
        out[cond] = {'runs':len(xs), 'tasks':len({x['task'] for x in xs}), 'successes':succeeded,
                     'success_rate':succeeded/len(xs), 'tokens_total':tokens,
                     'tokens_per_success_all_attempts':tokens/succeeded if succeeded else None,
                     'seconds_per_success_all_attempts':seconds/succeeded if succeeded else None,
                     'median_tokens':median(x['tokens'] for x in xs), 'median_seconds':median(x['seconds'] for x in xs)}
    base = {(x['task'],x['repeat']):x for x in groups.get('A',[])}
    pairs = {}
    for cond, xs in groups.items():
        if cond == 'A': continue
        matched = [(base[(x['task'],x['repeat'])],x) for x in xs if (x['task'],x['repeat']) in base]
        tasks = sorted({a['task'] for a,b in matched})
        diffs = {t:mean(float(b['success'])-float(a['success']) for a,b in matched if a['task']==t) for t in tasks}
        rng = random.Random(seed)
        boot = sorted(mean(diffs[rng.choice(tasks)] for _ in tasks) for _ in range(2000)) if tasks else []
        pairs[cond] = {'matched_pairs':len(matched), 'tasks':len(tasks),
                       'success_rate_difference':mean(diffs.values()) if tasks else None,
                       'task_bootstrap_95_interval':[boot[49],boot[1949]] if boot else None,
                       'note':'Exploratory task bootstrap; shared source/donor repositories are not independent.'}
    return {'conditions':out, 'paired':pairs}


def runtime_manifest():
    import platform
    import subprocess
    import importlib.metadata
    def version(args):
        try:return subprocess.check_output(args,text=True,timeout=10).strip()
        except Exception:return 'unavailable'
    try:pw=importlib.metadata.version('playwright')
    except importlib.metadata.PackageNotFoundError:pw='unavailable'
    return {'python':platform.python_version(),'platform':platform.platform(),'node':version(['node','--version']),
            'npm':version(['npm','--version']),'playwright':pw,'code_sha256':code_hash()}
