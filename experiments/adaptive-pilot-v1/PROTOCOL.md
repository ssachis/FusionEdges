# Eight-task adaptive pilot — ready to launch

**32 planned transfer runs:** 8 tasks × 2 approaches × 2 repeats. No transfer run or Gemini call has been made during this preparation.

| Host | Navigation | Footer | Card | Accordion |
|---|---|---|---|---|
| SkyCooper/ReactRouterWebpage, React 18 | 2 runs per approach | 2 per approach | 2 per approach | 2 per approach |
| JColeCodes/react-portfolio, React 17 | 2 runs per approach | 2 per approach | 2 per approach | 2 per approach |

Every source and donor repository is pinned to its full commit. Both approaches receive identical prompts, exact donor file names, explicit component props/content, and task acceptance criteria. The host React major and framework must remain unchanged.

## What is being transferred

- **Navigation:** ixartz `NavbarTwoColumns`, logo “Fusion Pilot,” Home and Documentation links. Replace only the host navigation; preserve the host hero, page content and underlying routes. The menu change is intentional.
- **Footer:** ixartz `CenteredFooter`, the same logo, Home/Privacy links, no social icons, and retained CreativeDesignsGuru attribution. Replace only the original footer.
- **Card:** timlrx `Card`, title “Pilot card,” description “A reusable component transfer.”, intentionally empty image prop, and a fixed example link. Add one card without replacing existing host cards.
- **Accordion:** muhammad-fiaz `Accordion`, single/collapsible, one fixed question and answer, initially closed. Click, Enter and Space must open and close it.

Declared data attributes and simple JSON props are wired mechanically by the rules layer. Identical props and locator requirements are present in the baseline prompt; this wiring is an explicit part of the adaptive intervention. Composed React children and behavioral integrations still require genuine integration work.

Each task specifies one common `data-fe-transfer` test locator. It carries no styling or implementation requirement and is given identically to both approaches. Static donor-code checks and independent visual review are still required: adding a locator alone is not a transfer.

## Validation completed before either arm ran

- Both unmodified source commits build successfully using dependency caches whose package manifests match the original sources.
- Original host content, existing cards and the portfolio hero/title pass semantic preservation assertions at 1280px and 390px.
- All four donor fixtures build and render, with the original component implementation bytes unchanged. Props match those requested in the pilot.
- Donor content/link assertions pass on desktop and mobile. Accordion click, Enter and Space assertions pass in open/closed states.
- Sixteen negative-control checks pass: all eight task contracts reject an untransferred original site at both widths while host preservation still passes.
- Reference screenshots, code/stylesheet fixtures, dependency locks and metadata are archived and hashed. No AI-created rendering is used as the donor reference.

**Known baseline defect:** the SkyCooper site already scrolls horizontally on mobile. Its baseline is recorded. New or worsening overflow is checked; an increase above four pixels is a regression. Reviewers must also assess the component’s own layout rather than treating this existing defect as permission for new overflow.

**Reference qualification:** navigation/footer references use the original Next 14 project with a reference page and static-export configuration. Card/accordion references use unchanged donor component code in an isolated Next 16 / React 19 fixture with donor CSS; the complete original donor application is not being benchmarked. The card donor itself pins Next 15. This fixture choice is recorded, not represented as a full original-app baseline.

## Frozen comparison

- **Baseline:** one-shot coding agent with up to two repairs.
- **Adaptive:** rules and verification first; a focused compatibility report if needed; at most one local edit, then a tool agent capped at eight steps if necessary.
- **Model:** Gemini `gemini-3.6-flash`, answer cap 4,096 tokens, no alternate provider/model fallback.
- **Historical memory:** disabled. The report may retrieve current source/donor code. Evaluation outcomes do not enter memory.
- **Order:** reproducibly shuffled within task/repeat, seed 261102; serial execution.
- **Warm setup:** identical original-source dependency cache and frozen npm lock in both approaches. Dependency changes detach the shared cache and perform a real install. Source browser baselines are prewarmed before timing.
- **Accounting:** include rules, report generation, edits, repairs, checks and waiting in run time. Count report and editing tokens, failures, and quota retries. Started/recorded/exception audit events are retained. An exception with missing usage requires inspection; do not assume zero.

This compares two complete systems. It is not an equal-call-budget causal test of the report. Stage-specific costs and escalation decisions will show where any difference comes from; report and memory ablations follow only if justified.

## Launch from the terminal where Gemini is ON

```bash
cd ~/Desktop/fusionedges-5
zsh experiments/run-adaptive-pilot.command
```

The launcher checks the Gemini key is visible, validates code/reference/setup hashes, and starts the batch. Quota exhaustion leaves progress saved; run the same command later to resume. Do not change rules, prompts, contracts, model, or frozen files mid-batch. A changed codebase requires a new pilot version.

Offline readiness check, with no AI calls:

```bash
python3 -m fusionedges.pilot experiments/adaptive-pilot-v1.json
```

## Quality and review

Automatic success requires a build, applicable static checks, the frozen component/host/interaction contract, target framework/React-major preservation, and desktop/mobile browser checks. Missing verification is not success.

The product endpoint additionally requires independent, label-blinded ratings of **2/2 for donor fidelity, host preservation, and responsive layout**, plus passing interactive behavior where applicable. Use two reviewers and adjudicate disagreements; missing ratings remain unverified. Assess allowed menu/footer replacement as an intended change, not a regression. Compare non-transferred host content against the archived host baseline. Record each reviewer’s arm guess because implementation style can reveal the approach despite hidden labels.

After all 32 cells complete:

```bash
python3 -m fusionedges.pilot experiments/adaptive-pilot-v1.json --review-packet output/adaptive-pilot-review
```

Do not score random light positions or the phase of the JCole typewriter animation as a regression; compare stable layout, content and styling. Explicit generator-attribution comments are removed from review diffs; functional code is retained and the raw diff remains in the original run. Structural code conventions can still reveal the approach.

Share only `output/adaptive-pilot-review/reviewer` with reviewers. Keep `PRIVATE-arm-key.json` separate. Packets contain component/full-page/interaction screenshots, diffs, donor references and host references. Missing builds/screenshots must be rated accordingly, not omitted.

Publish successes/16 per arm; visual success; build and regression rates; task-averaged quality; token/time distributions and total cost per successful transfer including failures/retries; changes to original host code; and routing/escalation counts. Show incomplete cells explicitly while the free-tier batch is still running.

## Limits

This is a development pilot on two previously seen hosts and three Next donors, with correlated tasks and only two repeats. It can find implementation problems and estimate the cost/quality trade-off for these cases. It cannot establish repository-independent generalization, durable superiority, or memory benefits. A later disjoint-repository benchmark is required.
