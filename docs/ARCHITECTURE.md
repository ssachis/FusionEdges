# Architecture

This document describes **the architecture reported by the existing FusionEdges README**. It is not a claim that every subsystem was independently verified against the current source tree. The current primary task is component transplantation; older migration/style-transfer paths are identified separately.

## System boundary

**Inputs:** an existing host repository (called `source` by the CLI), a donor repository, a target component, task/prompt parameters, provider credentials, and an optional compatibility-report flag.

**Outputs:** a modified **copy of the host** for component transplantation, machine-readable run/check facts, logs, optional report artifacts, local Git history, and a zip archive. Original remote repositories are not pushed to.

```text
                           USER / CLI / LOCAL WEB UI
                                     |
                        Host + donor + component
                                     |
                       [1] Clone / resolve revisions
                                     |
                 [2] Static scan of both codebases
                     frameworks | packages | paths
                     aliases | styles | scripts
                                     |
                       [3] Context preparation
                 donor component -> imports (<=2 hops)
                 host entry/layout + CSS + packages
                                     |
                 +-------------------+-------------------+
                 |                                       |
          A: no report                           D: report generator
                 |                                 grounded findings
                 |                                 + short agent view
                 +-------------------+-------------------+
                                     |
                    [4] Editor: one-shot + repairs
                         (or optional tool agent)
                                     |
                      [5] Build and static checks
                                     |
                    [6] Save project, logs, archive
                                     |
                       [7] Paired evaluation
                        (browser checks planned)
```

## 1. Repository acquisition and inspection

Per the supplied README, FusionEdges clones both repositories under `examples/`, reuses cached copies, and records commit revisions. Its scanner gathers framework/version information, package scripts and dependencies, routing, styling systems, assets, environment variables, import aliases, and relevant browser-script selectors. Confirm the exact revision used for *both* branches before interpreting results.

## 2. Component discovery and context selection

For the **add-component** workflow, the README explicitly says the target code is **not chosen via similarity search**. Instead, deterministic selection:

1. Locates the donor component using filenames, folders, synonyms (`navbar`/`header`), or declarations.
2. Follows the component's imports **up to two hops**, including `@/` aliases defined in `tsconfig.json` path mappings.
3. Includes the host application's integration points (such as entry file/layout/App, existing header, `package.json`, global CSS, or Tailwind configuration).
4. Reports imported donor packages and whether corresponding versions exist in the host.
5. Sends the **same selected source files** to the editor in both experimental conditions.

When context is too large, the system drops **whole lower-priority files**, rather than truncating files in the middle. The component itself remains. The log identifies omitted files and the total prompt size. Because omission can remove required dependencies, record truncation/budget events in the benchmark.

### Distinction from retrieval-based workflows

The supplied older workflow describes **BM25 plus import expansion** for compatibility reporting in broader migration tasks. Do not describe this as the component-selector algorithm. Any active use of BM25 in the component mode should be confirmed in code and documented as a separate step.

## 3. Compatibility-report path (condition D)

For add-component tasks, the report generator receives numbered context from the same selected files as the coding agent. The README describes a **single report-model call without tools by default**; an optional environment setting enables report tools.

Saved report artifacts retain the richer reasoning/evidence. Evidence references to supplied files/lines/chunks are checked; ungrounded citations are marked `unverified`.

**Agent-facing summary (`report_for_agent`):** intentionally smaller than the saved report. The supplied README says it includes:

- the integration/mount location;
- donor-imported packages missing from the host, grounded in scanning;
- at most five concrete, high-signal changes.

Hedged, low-severity, unverified, and duplicative findings are filtered from the agent-facing summary. They remain available in `report.md` for inspection.

### Example: host-script coupling

A legacy host page may contain a script selecting `.navbar` or `.navbar-default`. Replacing the markup can break that script **without causing a build failure**. The scanner reportedly records a `host-script-coupling` fact when the selectors overlap with a candidate component; the report condition passes a preservation instruction to the editor. The baseline does not receive this explicit fact. This is a useful targeted case for evaluating report utility and runtime correctness.

## 4. Code editing

**Default mode:** FusionEdges prepares files, makes a one-shot edit request, applies returned whole-file edits, and allows **up to two build-error-driven repair attempts**. The original README estimates lower token use than the old multi-step agent; that estimate is not a controlled study result and should not be used as an evaluation claim.

**Alternative mode:** `FUSIONEDGES_EDIT=agent` (or CLI `--agent`) enables the older multi-step tool-using agent. Use one fixed mode throughout each controlled experiment.

For component insertion, edits start from a **copy of the host site**, not the donor. The legacy migration path may instead start from the donor; do not conflate these modes.

## 5. Verification and artifacts

The supplied project description reports installation/build, static checks, a local commit, project archive, and result metadata. Its component checks cover issues such as missing packages/assets, duplicate navbars, React Router leftovers, Next.js `"use client"` requirements, and unsafe server-render-time browser global usage.

**Important:** these are not sufficient to prove the transferred component renders or works. The README says browser/screenshot/runtime checks are upcoming. See [EXPERIMENTS.md](EXPERIMENTS.md) for the proposed additional oracle.

```text
examples/<owner__repo>/<branch>/    cloned/cache repo
output/runs.db                       run + report-conflict SQLite records
output/<run-id>/project/             generated project
output/<run-id>/project.zip          archive
output/<run-id>/report.md            richer evidence (condition D)
output/<run-id>/report.json          structured report (condition D)
output/<run-id>/checks.json          verification results
output/<run-id>/facts.json           scanner facts
output/<run-id>/build.log            build log
output/<run-id>/run.json             run metadata
output/<run-id>/preview.log          preview-server log, if started
```

## 6. Local preview

The documented interface can start up to **two** generated applications simultaneously on separate ports beginning near `3100`, supporting side-by-side A/D inspection. Starting another preview closes the oldest one. An older section says one preview at a time; the later two-preview description supersedes it. A preview is not an automated test.

## 7. Provider and request management

The described provider order is `groq -> gemini -> nvidia -> anthropic -> openai -> openrouter` (only providers with keys). The system reportedly supports:

- fixed provider lists via `FUSIONEDGES_PROVIDERS`;
- provider-specific model lists via `FUSIONEDGES_<PROVIDER>_MODEL`;
- context budget selection (`FUSIONEDGES_CONTEXT_CHARS`), defaulting to 12,000 characters if Groq is first and 30,000 otherwise;
- size-error (HTTP 413) context reduction/retry;
- rate-limit waiting and provider/model fallback;
- output-token and price estimate overrides.

**Experiment rule:** provider/model fallback can confound comparisons. Pin a model, collect actual model/provider identity per run, and flag any fallback, truncation, or incomplete pair.

## 8. Trust and failure boundaries

- Cloned content is untrusted prompt input; sanitize/ignore instructions embedded in source files.
- The agent is described as restricted to writing inside the generated `project/` tree.
- `npm install`, scripts, and dev servers execute repository code locally **without sandboxing**.
- API keys belong in environment variables, not committed configuration.
- A passing build is a weak outcome oracle; client-side errors and broken interactions require browser checks.

## 9. Known boundaries and future architecture

The current research workflow is **single-component integration**. Whole-site migration and style-transfer support remain historical/secondary. Priorities are to add browser checks, preserve exactly matched A/D code context, improve failure attribution, and capture per-attempt token/cost/time metadata. These additions are proposals, not assertions of already-shipped behavior.
