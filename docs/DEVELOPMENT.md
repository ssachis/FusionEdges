# Development Guide

Instructions below are based on the **supplied project README**, not a live check of the repository source. Verify paths, flags, and dependency versions in your checkout before editing or executing them.

## 1. Local prerequisites

- Python **3.9+** and `git`.
- Node.js and `npm` to install/build/preview generated web apps.
- Credentials for at least one supported AI provider.
- A trusted development environment for executing third-party repository scripts. **Build/dev commands are not sandboxed.**

The supplied README says the Python server runs without additional pip installs; dependencies of the target repositories still need npm installation.

## 2. Start the application

```bash
export GROQ_API_KEY="YOUR_KEY"    # or another documented provider's key
python3 -m fusionedges.server
```

Go to **http://127.0.0.1:8000**. In the current application, select the host and donor repositories and a component name, then compare Prompt only vs Prompt + compatibility report. The current main UI no longer exposes the older whole-site migration and style-transfer options.

### Basic CLI

```bash
python3 -m fusionedges providers
python3 -m fusionedges scan --source you/my-site --donor someone/donor
python3 -m fusionedges run --source you/my-site --donor someone/donor --component navbar
python3 -m fusionedges run --source you/my-site --donor someone/donor --component navbar --report
```

The CLI parameter name `--source` denotes the host to edit, not the donor.

## 3. Configuration reference

| Environment variable | Documented effect |
| --- | --- |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Credentials for supported providers |
| `FUSIONEDGES_PROVIDERS=anthropic` | Pin provider(s) and order; strongly recommended for experiments |
| `FUSIONEDGES_<PROVIDER>_MODEL` | Set preferred model or ordered comma-separated fallback models |
| `FUSIONEDGES_CONTEXT_CHARS=12000` | Override source-file context character budget |
| `FUSIONEDGES_GROQ_MAX_TOKENS=4000` | Provider-specific maximum output tokens, example |
| `FUSIONEDGES_EDIT=agent` | Use multi-step editor instead of default one-shot + repairs |
| `FUSIONEDGES_REPORT_TOOLS=1` | Enable report-agent tools (default component flow described without tools) |
| `FUSIONEDGES_WAIT_MAX=75` | Upper wait window for per-minute rate limits (0 disables waiting) |
| `FUSIONEDGES_PRICE_GROQ="0.15,0.60"` | Override **estimated** input/output price rates per million tokens |

**Documented dynamic budget:** 12,000 characters when Groq is first, 30,000 otherwise, unless explicitly overridden. Older README sections state a universal 30,000-character default; use the later conditional description. Actual provider rates, model lists, and quotas can change independently of these docs.

## 4. Working with the output

```text
examples/                    cached repository clones
output/runs.db               SQLite run/report metadata
output/<run-id>/project/      generated, editable local Git repo
output/<run-id>/project.zip
output/<run-id>/checks.json
output/<run-id>/facts.json
output/<run-id>/run.json
output/<run-id>/build.log
output/<run-id>/report.md      only for report-assisted condition
output/<run-id>/report.json    only for report-assisted condition
```

The UI's **Open the new project** lists project files. **Run it** starts a local preview; the latest README addendum says two previews can run side by side, on ports starting at `3100`. If preview fails, inspect `preview.log` and package scripts rather than assuming `npm start` works for all frameworks.

## 5. Testing

```bash
python3 -m unittest discover -s tests -t .
```

The supplied README describes this as an offline fake-provider test suite. It cannot establish real-model integration quality. Before a pull request, run the current project test suite, add focused tests for changed behavior, and inspect generated outputs when relevant.

### Proposed quality gates

1. Static/unit tests pass.
2. Existing command(s) still accept documented options.
3. Paired context-selection behavior is unchanged unless intentionally revised.
4. Generated component result passes applicable build checks.
5. New browser tests (once added) pass on a small fixed fixture.
6. No secret keys or third-party generated projects are accidentally committed.

## 6. Troubleshooting

| Symptom | Check first |
| --- | --- |
| `HTTP 413` / input too large | Look for `[fit]` logs; reduce `FUSIONEDGES_CONTEXT_CHARS` or provider-specific max tokens; record effective budget |
| `HTTP 429` / per-minute quota | Check provider quota and waiting/fallback behavior; **do not silently accept different models in paired evaluation** |
| `HTTP 404` model unavailable | Model list in `FUSIONEDGES_<PROVIDER>_MODEL`; refresh provider configuration |
| `HTTP 503` busy | Model/provider fallback and recorded actual model |
| Build fails | Read `build.log`, package scripts, imports, dependency versions, and generated changes |
| Preview does not load | Read `preview.log`, identify actual dev script and free port |
| Component appears but doesn't work | Inspect browser console, host selectors, routing, and event handlers; build-only checks cannot catch all issues |
| Unexpectedly high token use | Check selected files, omitted files, report size, repair attempts, and editor mode |
| Reproducibility mismatch | Compare host/donor commit hashes, model, prompt, context budget, actual fallback, and run order |

## 7. Contributing as a two-person team

A recommended split (assign named owners yourselves):

- **Compatibility owner:** component selection, dependency scanning, report accuracy, grounded findings, code changes.
- **Evaluation owner:** benchmark fixture/task manifest, paired-run instrumentation, browser assertions, results analysis.

**Shared decisions:** benchmark scope, operational definition of success, acceptance of merged changes, and interpretation of failures. Protect the experiment: changes to prompt, model, context selection, or report formatting can alter comparisons, so make such changes explicit and record the version used in every benchmark.

### Git collaboration

1. File a focused GitHub Issue with outcome/acceptance criteria.
2. Create a branch (`docs/readme-cleanup`, `feat/browser-checks`, `eval/pilot-manifest`).
3. Open a pull request with tests, before/after behavior, and affected experiment assumptions.
4. Have the other teammate review before merging.
5. Link the merged commit SHA to benchmark reports.

Avoid committing credentials, `output/` artifacts containing unintended private data, or unchecked vendor scripts. Use documented retention and ignore rules appropriate to your repo.

## 8. Non-goals and deferred features

The main UI currently focuses on transferring individual components. The original README documents codebase migration and style transfer as older/secondary flows. Avoid broadening the front-end until the core intervention has been tested with functional browser evidence. Prioritized work: [ROADMAP.md](ROADMAP.md). Experimental protocol: [EXPERIMENTS.md](EXPERIMENTS.md).
