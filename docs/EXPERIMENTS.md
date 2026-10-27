# Experiments and Evaluation Protocol

**Status: proposed evaluation protocol.** The supplied FusionEdges README describes a runner, result persistence, static checks, and paired conditions, but it does **not** supply measured real-provider results establishing a benefit from compatibility reports. Record actual results before changing any statement below into a finding.

## 1. Research question and hypothesis

**Question:** On a matched UI component transplantation task, does a structured compatibility report improve the coding agent's integration quality compared with the same selected code and prompt alone?

- **H0:** the report does not change the probability of successful integration (or could worsen it).
- **H1:** the report increases successful integrations and/or reduces avoidable compatibility failures.
- **Secondary questions:** What is the effect on cost, tokens, time, repair count, and failure categories? For which mismatch types is the report most helpful?

## 2. Experimental conditions

| Attribute | A — Baseline | D — Treatment |
| --- | --- | --- |
| Target host and donor commits | Fixed | Same fixed commits |
| Requested component | Same | Same |
| Edit prompt and selected code context | Same | Same |
| Coding model/provider, settings, budgets | Fixed | Same |
| Coding mode | One-shot + repairs | Same mode |
| Pre-edit compatibility report | **Absent** | **Present** |
| Verification and hardware | Same | Same |

The report-generation step itself adds model effort and cost. **Do not pretend the total amount of information or total tokens is identical**: the added compatibility report is the intervention. Report both editing-only and end-to-end costs where possible.

### Threats to validity

Model sampling, provider fallbacks, automatic context trimming, repo updates, different input-code selection, repairs, contamination from cached state, and provider throttling may affect outcomes. Freeze commits and configurations; audit metadata for deviations; never quietly mix provider models.

## 3. Benchmark selection

### Pilot (first)

Select **5 representative repository pairs** with a clear navbar/header donor and a host that can reasonably integrate it. This is a **proposed** pilot size, not a completed sample. Include both straightforward and challenging mismatches:

1. React-to-React with similar styling.
2. React-to-React with different CSS/Tailwind conventions.
3. React-to-Next.js with client/server component boundaries.
4. Import-alias and/or external-package differences.
5. Legacy host script selectors coupled to the existing navbar.

Choose legal, public, maintainable repositories; pin commit hashes; inspect licenses; avoid installing scripts from untrusted projects outside a sandbox. Record exclusion decisions **before** evaluating treatment outcomes.

### Larger evaluation (after pilot)

Expand to a diverse, pre-registered task set informed by the pilot's feasibility and observed variance. Use multiple runs/seeds per pair if provider stochasticity is material. Do not choose a sample size solely because preliminary D results look favorable.

## 4. Task manifest (proposed schema)

Store a machine-readable manifest **if the existing bench runner already supports one, adapt its actual format rather than replacing it**. Example conceptual structure:

```yaml
# Illustrative schema only; not guaranteed accepted by FusionEdges CLI.
task_id: navbar-react-next-001
host_repo: owner/host
host_commit: <full-git-sha>
donor_repo: owner/donor
donor_commit: <full-git-sha>
component: navbar
category: react-to-next
expected_mount: app/layout.tsx
expected_behavior:
  - navbar_visible
  - primary_links_navigate
  - no_duplicate_header
```

Record license, framework versions, expected component location, dependency differences, and any manually defined acceptance tests before running AI.

## 5. Execution procedure

1. Validate access and buildability of each frozen input repository, noting any baseline failures.
2. Run local project tests (fake-provider tests); do not use this as evidence that the real provider succeeds.
3. Fix one coding model/provider and generation settings for **both** conditions.
4. Verify A and D receive identical selected code and edit prompt; log hashes or diffs of the **common context**.
5. Execute A and D for every task with matched limits and isolated output directories. Randomize or alternate A/D run order to mitigate time/rate-limit effects.
6. Collect output repositories, report files, logs, model IDs, status, token usage, repair attempts, elapsed time, and estimated provider cost.
7. Apply **the same** independent verification suite to A and D; evaluate without using treatment information to relax checks.
8. Record errors, retries, context reduction, provider fallback, and missing data explicitly.
9. Inspect failures qualitatively; annotate exact compatibility issue and link it to observable evidence.
10. After a pilot, revise the benchmark **once, transparently**, then freeze it for the larger experiment.

### Example CLI invocations

```bash
# Select ONE provider for a fair pair; choose a stable model for that provider.
export FUSIONEDGES_PROVIDERS=anthropic

python3 -m fusionedges run --source owner/host --donor owner/donor --component navbar
python3 -m fusionedges run --source owner/host --donor owner/donor --component navbar --report
```

**Caution:** the CLI snippets here follow the supplied README. Check whether your checkout accepts commit pins, seeds, and other study parameters; if it does not, record/pin them externally. Do not assume a `bench run` or `bench report` invocation without checking the local CLI help.

## 6. Outcome definitions

**Primary metric (proposed): independently verified successful integration.** Success requires all of:

- build and package installation succeed;
- target component is visibly rendered **once** at the intended mount point;
- essential component interactions work (e.g., navbar links/menu);
- required host functionality remains operational;
- no relevant uncaught browser errors occur during the prescribed test.

Evaluate all criteria with a shared rubric. A build-only success is **not** a primary success.

| Metric | Definition / collection | Status |
| --- | --- | --- |
| Build success | `npm run build` completes | Described as implemented |
| Static compatibility checks | Facts/checks artifacts; package, wiring, route/style issues | Described as implemented |
| Functional success | Playwright/browser test or equivalent rubric | **To implement** |
| Browser errors | Console / page exceptions under test scenarios | **To implement** |
| Visual regression | Screenshots compared to expected structure, reviewed using predefined criteria | **To define/implement** |
| Token use | Input + output tokens, with report/edit/repair subtotals if available | Log or instrument |
| Latency | End-to-end wall time and editor-only time | Log or instrument |
| Estimated cost | Provider rate assumptions x billed/recorded tokens | Log or instrument |
| Repairs | Number of edit/fix attempts | Described edit loop; verify recording |
| Report validity | Evidence-backed vs unverified findings and actual issues caught | Audit manually/sample |

Visual match is more nuanced than pixel equality: a donor style integrated into a host may intentionally differ in layout. Define the expected style and acceptance rubric per task *before* judging outputs.

## 7. Example browser assertions (to implement)

This is **illustrative test logic**, not a drop-in test already present in the repository:

```python
# Pseudocode: adapt selectors/paths to each pinned benchmark task.
page.goto(local_preview_url)
assert page.locator("header nav").count() == 1
assert page.locator("header nav").is_visible()
page.get_by_role("link", name="About").click()
assert expected_destination_matches(page.url)
assert not captured_uncaught_page_errors
```

Also test responsive navigation and any original host-page behavior at risk (for example, jQuery handlers binding to old `.navbar` selectors).

## 8. Recording results

Suggested flat reporting columns:

```csv
task_id,trial,condition,host_commit,donor_commit,provider,model,context_hash,build_pass,functional_pass,host_regression_pass,browser_errors,repair_count,input_tokens,output_tokens,estimated_cost_usd,elapsed_seconds,fallback_occurred,context_reduced,failure_category,notes
```

No example values are included: do **not** fabricate observations. Store raw logs, output projects, and precise reason codes for missing measurements. Record explicit timeouts as failures only if that is in the predeclared outcome policy.

## 9. Statistical analysis

- For the pilot, show **paired task-level outcomes** and concrete failure traces; do not overinterpret small samples.
- For a larger set, compute condition-wise primary success rates and **paired difference**, with appropriate uncertainty intervals (e.g., bootstrap resampling over tasks).
- For paired binary task results, optionally report discordant-pair counts and an exact paired test; account for repeated runs per task rather than treating all trials as independent.
- Compare paired cost/time/token deltas using medians or distributions; separate report-generation overhead from editor savings.
- Report negative cases (D worse than A), not only wins.
- Predefine treatment of invalid runs, rate limits, provider fallback, and build failures.

## 10. Failure taxonomy

Suggested mutually understandable categories (a result may carry multiple tags): missing package; unresolved import/alias; routing mismatch; Next.js client/server boundary; CSS conflict; duplicate component; missing asset; host-script coupling; build error; runtime exception; nonfunctional interaction; context budget omission; model/provider interruption; report misinformation.

For every D win, ask **which report fact changed the code path?** For every D loss, ask whether the report was wrong, redundant, distracting, or expensive.

## 11. Reporting template

```markdown
# FusionEdges Evaluation — <date / frozen commit>

## Dataset
- Tasks / repositories: ...
- Inclusion and exclusion rules: ...
- Model, provider, edit mode, budget: ...

## Primary results
- Verified successes A: ... / ...
- Verified successes D: ... / ...
- Paired difference and uncertainty: ...

## Secondary results
- Build success / runtime errors: ...
- Tokens, elapsed time, cost (including report): ...

## Failure analysis
- Cases helped: ...
- Cases harmed: ...

## Limitations
- Sample selection, model/provider, reproducibility, measurement gaps: ...
```

## 12. Exit criteria for research claims

Do not claim the report improves outcomes until a frozen task suite, complete paired runs, browser-aware outcome checks, recorded provider identity, and transparent failure accounting support the conclusion. If the evidence is inconclusive, report that finding.
