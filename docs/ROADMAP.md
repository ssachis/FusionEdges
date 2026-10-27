# Roadmap

**Purpose:** take FusionEdges from a functioning component-transplant demo to a reproducible, evidence-based evaluation of compatibility reports. This is a **proposed work plan**, not a changelog or claim that milestones have shipped.

## Current baseline (from supplied README)

**Described as implemented:** repository clone/scan, donor component discovery and import tracing, source wiring-point selection, report generation with grounded evidence, short `report_for_agent`, one-shot editing plus up to two build repairs, optional multi-step agent, static/build checks, output persistence, provider fallback, two side-by-side local previews, and fake-provider unit tests.

**Not yet demonstrated in supplied material:** a completed real-provider benchmark proving that the report improves transfer success; automated browser/runtime checks; independently verified visual/function success measurements.

## Owners

| Role | Main ownership | Partner responsibilities |
| --- | --- | --- |
| **Contributor A — Compatibility** | Repo scanning, component context, report content and provenance | Review experiment protocol and failure cases |
| **Contributor B — Evaluation** | Benchmark tasks, fair A/D execution, browser checks, results and analysis | Review report changes and failure cases |
| **Both** | Scope, definition of success, code review, final research claims | Shared approval |

Replace role labels with actual contributor names in project issues. Neither person owns the research interpretation alone.

## Milestone 0 — Documentation and agreement

**Goal:** a new contributor can reproduce one baseline and treatment task and understand why the comparison is fair.

- [ ] **A:** Merge `README.md` and `docs/ARCHITECTURE.md`; check every code path against the actual checkout.
- [ ] **B:** Merge `docs/EXPERIMENTS.md`; select the initial outcome rubric and required metadata.
- [ ] **Both:** Resolve historical vs current workflows in CLI/UI documentation.
- [ ] **Both:** Agree that the primary outcome includes actual rendered/functioning UI, not only a passing build.

**Exit criterion:** both contributors independently follow setup instructions and explain the A/D difference consistently.

## Milestone 1 — Controlled real-provider pilot

**Goal:** establish that the benchmark can run end to end and surface interpretable failures.

- [ ] **B:** Freeze an initial set of **5** public, trusted host/donor component tasks, including straightforward and cross-framework cases; pin commit hashes and document acceptance requirements.
- [ ] **A:** Audit same-code-context guarantees for A and D, deterministic file selection, and recording of dropped files.
- [ ] **B:** Pin a single model/provider, editor mode, context budget, and timeout policy; run A/D pairs.
- [ ] **A:** Inspect each generated report for factual grounding, omitted relevant facts, and misleading instructions.
- [ ] **Both:** Preserve runs, logs, actual model identity, cost/time data, and specific failure cases.

**Exit criterion:** every selected task has a documented A/D outcome or a transparently recorded infrastructure failure. **A pilot does not establish superiority.**

## Milestone 2 — Functional runtime evaluation

**Goal:** distinguish code that merely builds from integrations that actually work.

- [ ] **B:** Add repeatable headless browser checks (e.g., Playwright) for navigation visibility, uniqueness, interactions, and JavaScript errors.
- [ ] **A:** Provide robust mount-point/selector expectations or task annotations for assertions.
- [ ] **B:** Include host regression checks (especially scripts relying on existing selectors).
- [ ] **Both:** Validate checks against a known-good and a deliberately broken integration.

**Exit criterion:** the same runtime evaluator produces meaningful pass/fail evidence for A and D, with failure screenshots/logs where appropriate.

## Milestone 3 — Benchmark expansion and analysis

**Goal:** answer the research question with reproducible paired evidence.

- [ ] **B:** Freeze an expanded, diverse benchmark and the analysis plan before inspecting its final outcomes.
- [ ] **A:** Freeze report/prompt behavior; version any changes that might affect treatment strength.
- [ ] **B:** Run paired trials; report success rates, discordant outcomes, uncertainty intervals, and cost/time deltas.
- [ ] **A:** Classify report-specific failure mechanisms and inspect counterexamples where D fails and A succeeds.
- [ ] **Both:** Separate observed results, causal interpretations, and limitations.

**Exit criterion:** a complete results report with raw artifact references and a reproducible methodology. If evidence is inconclusive, say so.

## Milestone 4 — Final research deliverables

- [ ] **A:** Architecture narrative, system diagrams, report examples, component-dependency case studies.
- [ ] **B:** Methodology, benchmark description, tables/plots, statistical analysis, threats to validity.
- [ ] **Both:** Related-work discussion and contributions/limitations.
- [ ] **Both:** Prepare a five-minute walkthrough: problem -> baseline -> report intervention -> example -> measured results.

**Exit criterion:** another researcher can understand the intervention and repeat the evaluation with accessible tasks, configuration, and artifacts.

## Recommended working order

**Do next:** update the docs; choose the **primary success rubric**; freeze 5 candidate tasks; audit A/D context matching; run a small paired pilot. **Do not prioritize broader migration/style-transfer features before the component evaluation is credible.**

## Backlog (only after core evidence)

- More components beyond navbars (footer, cards, modals) with component-specific acceptance tests.
- More host frameworks, dependency configurations, and alias patterns.
- Cross-model replication, repeated trials, and robustness experiments.
- Better report ablations (e.g., only missing dependencies vs full report).
- Optional retrieval/agent strategies and broader migration support.
- Security hardening/sandboxed execution for untrusted repository builds.

## Open decisions to document

| Decision | Recommended default |
| --- | --- |
| Primary metric | Browser-verified functional integration plus build and host regression checks |
| Pilot sample | 5 diverse pinned tasks, feasibility only |
| Experiment mode | One-shot + repairs, same for A and D |
| Provider/model | One pinned provider + model for a complete comparison |
| Multiple trials | Add when stochastic variation is measured or suspected |
| Treatment overhead | Report it explicitly, rather than excluding report tokens/cost |
| Failures | Record both model mistakes and infrastructure failures |
| Results language | “Observed” until a reproducible study supports broader claims |

## Suggested issue labels

`documentation`, `compatibility`, `evaluation`, `browser-tests`, `benchmark`, `bug`, `research`, `good-first-issue`.
