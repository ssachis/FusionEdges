# FusionEdges

Convert **your site** into a **donor repo's codebase and style** with an AI agent, and measure whether
giving the agent a **compatibility report** first makes the migration work better.

## Run it

```bash
export GROQ_API_KEY=...          # set any of these; more = fewer interruptions
export GEMINI_API_KEY=...
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
python3 -m fusionedges.server    # opens http://127.0.0.1:8000  (Python 3.9+, no pip installs; needs git; npm to build/run results)
```

1. Pick a tab:
   - **Prompt only**: the agent gets the two repos and your prompt.
   - **Prompt + compatibility report**: FusionEdges first retrieves the relevant code from both repos (RAG)
     and writes a report (page map, style map, conflicts), then sends it to the agent with the same prompt.
2. Paste **your site** (GitHub link) and the **donor repo** (GitHub link).
3. Tick **Codebase migration**, **Style transfer**, or both, or **Add a component** and type its name
   (e.g. `navbar`). The prompt box fills with a default prompt for that choice; edit it if you like
   (Reset brings the default back).
4. Press **Run**. When it's done, click **Open the new project**, or **▶ Run it** to start it locally and open it.

Run the same pair in both tabs to compare them; **Recent runs** lists every run.

## Add a component (cheapest, recommended for experiments)

"Add the navbar from this React repo to my Next.js site." The output starts as a copy of **your site**.
Checks: something was added, it is rendered, no react-router left, `"use client"` where hooks/handlers are used,
no `window`/`document` while rendering, global CSS only in the root layout, imported packages are in
package.json, images exist, only one navbar/header on the page, and it builds.

## One-shot + repairs (default) vs full agent

By default the AI does not explore with tools. FusionEdges retrieves the relevant files itself, sends them in
one request, and applies the whole files that come back. If the build fails, the errors go back for at most
2 fixes. This uses roughly 10-20x fewer tokens than the multi-step agent. Both tabs get the same retrieved
files; the report tab adds the compatibility report. For the old multi-step agent (good for demos), set
`export FUSIONEDGES_EDIT=agent` before starting the server, or use `--agent` on the command line.

### What the agent is shown for "Add a component"

Similarity search is not used to pick the code here: moving a component needs *everything it imports*, so
FusionEdges finds the component in the donor (by file name, folder, synonyms such as navbar/header, or a
declaration), follows its imports two hops (including `@/` aliases from `tsconfig.json` `paths`), and adds only
the source's wiring points: `package.json`, the entry/layout/App file, the existing navbar (to replace), and global
CSS or the Tailwind config. It also lists the packages the donor files import with the source's version or `MISSING`.
The selection is deterministic and identical with and without the compatibility report.
The log prints the file count, characters and estimated tokens of the whole first message, plus any file left out for size.

```bash
export FUSIONEDGES_CONTEXT_CHARS=30000     # file budget (default 30000 chars, about 8,500 tokens)
export FUSIONEDGES_GROQ_MAX_TOKENS=4000    # optional: cap the answer size asked from one provider
```

Over budget, whole files are dropped (never cut in half), least useful first: two-hop imports, global CSS, the largest
one-hop imports, then the source's existing component, entry/layout and `package.json`. The component itself is kept.
Dropped files are named in the log and shown to the model as NOT SHOWN.

Groq's free/base limit is 8,000 tokens per minute per organization for gpt-oss-120b, gpt-oss-20b and qwen3 (a new key
from the same organization does not change it). For Groq use `FUSIONEDGES_CONTEXT_CHARS=16000`, about 6,400 Groq tokens
for the navbar example. The answer and any repair call also count against that minute, so a repair may fall through
to the next provider.

If a provider answers HTTP 413, read its message: it names the limit and the requested size. Lower
`FUSIONEDGES_CONTEXT_CHARS` and/or that provider's `..._MAX_TOKENS`. FusionEdges also moves on to the next provider.

## What each run does

1. Clones both repos into `examples/` (commit recorded).
2. Scans both (no AI): frameworks and versions, routing, styling systems, scripts, assets, env vars, aliases.
3. *(report tab)* Retrieves code chunks with BM25 + one-hop import expansion, then the report agent
   writes the report. Every cited file/line/chunk is checked; invented ones are marked `unverified`.
4. Creates `output/<run>/project/`: a copy of the **donor** for a migration (keep its framework, config and
   styles; replace its content with yours), or a copy of **your site** for style transfer only.
5. The AI writes the result there (one-shot + up to 2 build repairs, or the full agent).
6. Installs, builds and checks the result, commits it to a local git repo (ready to push to GitHub later),
   and zips it.

## Seeing the running site

On a finished run press **▶ Run it**. FusionEdges installs the packages if needed, starts the project's `dev`/`start`
script on a free port from 3100 (Create React App gets it through `PORT`, Vite/Next.js through `--port`), opens a tab,
and leaves an **Open http://localhost:31xx** link next to the button. "Open the new project" only lists the files.
One preview runs at a time; starting another stops the previous. If the page does not load, read
`output/<run>/preview.log` (the dev server's output) or run it yourself:
`cd output/<run>/project && npm install && npm start`.

## Checks (how a run is scored)

| Check | Pass when |
|---|---|
| Every source page has a route | each `page.html` has a page in the result (`about.html` → `/about`) |
| Source text kept | ≥ 90% of your pages' text blocks appear in the result |
| No .html links left | migration only: internal links use the new routes |
| Source images/fonts present | every image/font your site uses was copied |
| Uses donor styles, not source CSS | style transfer: ≤ 25% of your CSS rules copied, donor style files kept |
| Source look kept | migration without style transfer: ≥ 75% of your CSS rules ported |
| Builds | `npm run build` passes |

Outcome: **All checks passed**, **Some checks failed**, or **Failed** (build fails or more than half the pages are missing).
These are static checks; screenshots and browser checks are the next step.

## Folders

```
examples/                  cloned repos (owner__repo/<branch>/), reused across runs
output/
  runs.db                  every run + every report conflict (SQLite)
  <date>-<tab>/
    project/               THE RESULT: the new codebase (git repo)
    project.zip
    report.md report.json  compatibility report (report tab only)
    checks.json facts.json build.log run.json
```

## AI providers

Tried in order **groq → gemini → nvidia → anthropic → openai → openrouter**, only those with a key. On a rate limit,
quota, auth error, too-large request or outage the provider rests and the same call goes to the next one.
Change the order with `FUSIONEDGES_PROVIDERS=anthropic,groq`, models with `FUSIONEDGES_GROQ_MODEL=...` etc.,
prices with `FUSIONEDGES_PRICE_GROQ="0.15,0.60"` ($ per million input, output tokens; estimates).
For a fair comparison between tabs, use one provider (`FUSIONEDGES_PROVIDERS=anthropic`): the result page
lists the models each run used.

## Command line

```bash
python3 -m fusionedges providers
python3 -m fusionedges scan --source you/my-site --donor someone/next-site
python3 -m fusionedges run  --source you/my-site --donor someone/next-site            # prompt only
python3 -m fusionedges run  --source you/my-site --donor someone/next-site --report   # with report
python3 -m fusionedges run  --source you/next-site --donor someone/react-app --component navbar --report
```

## Benchmark (many tasks, comparable results)

Run the same tasks with and without the report and get a paired comparison.

```bash
# one JSON object per line: id, source, donor, component, optional category
python3 -m fusionedges bench run --tasks benchmark/tasks.example.jsonl --name pilot --reps 3
python3 -m fusionedges bench report --name pilot --baseline A     # rebuild the report only
```

- Conditions: `A` agent alone, `D` agent + report (`--conditions A,D`). Job order is shuffled with `--seed`.
- Resumable: every finished run is appended to `output/bench/<name>/results.jsonl`; rerun the same command to continue after a crash or rate limit. `--limit N` runs only N jobs.
- Output in `output/bench/<name>/`: `report.md` (per-condition table, paired differences with bootstrap 95% intervals and an exact sign test, per-category and per-task tables, failed runs), `results.csv`, `summary.json`.
- Use one provider (`FUSIONEDGES_PROVIDERS=...`) so the comparison is fair; the report warns if runs used different models.
- Success means the static checks all passed. Browser/runtime checks are not part of the score yet.

## Tests

```bash
python3 -m unittest discover -s tests -t .   # fake AI provider, no network
```

## Safety

Repo contents are treated as untrusted data in the prompts, and the agent can only write inside `project/`.
`npm install` / `npm run build` / `npm run dev` execute code from the repos on your machine and are not
sandboxed: only use repos you trust. Keys stay in your terminal environment. Nothing is pushed anywhere.


The report agent gets the same files as the editor for "add a component" tasks (the donor component, what it imports, and the source's wiring points), numbered so it can cite lines. Both conditions therefore see identical code; the report is the only difference.

For "add a component" the report agent makes one call with no tools (the component's code is already in the prompt), which keeps it small enough for Groq's free 8K tokens-per-minute limit when `FUSIONEDGES_CONTEXT_CHARS` is about 12000. Set `FUSIONEDGES_REPORT_TOOLS=1` to give it tools again.

When every provider is rate-limited by a per-minute limit (HTTP 429), FusionEdges waits for it to clear (up to `FUSIONEDGES_WAIT_MAX` seconds, default 75; 0 turns it off) and retries, twice at most. Size errors (413) and long quotas are not waited on.

The app now covers one task: add a component from a donor repo to your site (migration and style transfer were removed from the screen; the code still supports them). The default context budget is 12000 chars when Groq is the first provider and 30000 otherwise; `FUSIONEDGES_CONTEXT_CHARS` overrides it. The coding agent receives the report without citations and evidence snippets (`report_for_agent`); the saved report.md keeps them.

What the coding agent receives from the report is deliberately short (`report_for_agent`): where the component mounts, the packages the donor files import that the source lacks (taken from the scan, not from the model), and at most five concrete changes. Findings that are hedged ("none detected", "potential", "audit"), unverified, low severity or repeats of another finding are left out; they stay in report.md.

If a provider refuses a request as too large (HTTP 413), the report and coding steps cut the amount of code shown in proportion to how far over the limit it was and try again (up to 3 tries, never below 6000 chars). The log shows each `[fit]` step and the result records the budget actually used.

Free option with room for the full code context: a developer key from build.nvidia.com (no card). `export NVIDIA_API_KEY=...`, optionally `FUSIONEDGES_NVIDIA_MODEL=<id from the catalog>`, and pin it with `FUSIONEDGES_PROVIDERS=nvidia` so every run of an experiment uses the same model.

### Model fallback

`FUSIONEDGES_<PROVIDER>_MODEL` accepts several models, comma-separated, best first (for example
`FUSIONEDGES_GEMINI_MODEL=gemini-3.6-flash,gemini-3.5-flash-lite`). If a model is retired (HTTP 404) the next one
is used at once; if it stays busy (HTTP 503) after two waits, the next one is used. Gemini ships with
`gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`, so you do not need to set anything.

### Previews side by side

"Run it" keeps up to two results running at once, each on its own port (3100, 3101, ...), so you can open the
agent-alone and the agent-plus-report results next to each other. Starting a third closes the oldest.

### The site's own scripts

Old sites load jQuery-style scripts from `index.html` that look up elements by class or id (`$(".navbar").offset().top`).
Replacing the component that carries those classes makes the script throw at runtime, and the build never sees it.
The scanner now reads those scripts, keeps the selectors the component's source files also use, and records them as a
`host-script-coupling` fact. The report always passes it to the coding agent ("Keep: ... .navbar, .navbar-default").
Agent-alone runs do not get it, so the report stays the only difference between the two conditions.
# FusionEdges
