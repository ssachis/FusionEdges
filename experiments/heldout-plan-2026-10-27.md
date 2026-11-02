# Held-out experiment plan (frozen 27 Oct 2026, before any held-out run)

Written before screening or any held-out result, as the proposal requires (sec. 5, "Freeze the task set, success
criteria and annotations ... before any pipeline results exist"). Changes after this point are listed at the end
with a date and a reason.

## Question
H1: does the compatibility report (D) give a higher verified success rate and fewer host regressions than the
same agent alone (A)? Secondary: does that hold for a multi-step agent with tools (A2 vs D2)? Cost: tokens, and
repairs until the page works (build + browser repairs), as the "time to a working transfer".

## Tasks
- Navbar from a Next.js + Tailwind donor into a React site (CRA or Vite, react-router). 16 candidates:
  8 sites x 2 of 4 donors, each donor used 4 times (`heldout-screening.json`, commits pinned).
- None of these repos were used in the pilot; the pilot tasks are practice tasks.
- Chosen to be lesser-known (few stars) to limit memorisation; most have a hidden constraint the scanner flags:
  a global CSS selector leak (donor CSS restyles the site) and/or an existing navbar that must be replaced.
- Screening: agent alone, 2 runs each. Drop a task the baseline always passes (no headroom) or never gets working
  (no signal). Keep the first 12 remaining in file order. No other reason to drop a task.

## Conditions and controls
- A: agent alone (one-shot + up to 2 repairs). D: same + compatibility report. Same retrieved code for both.
- A2 / D2: the multi-step agent with tools, alone / with the report (first 4 kept tasks).
- One model for everything: gemini-3.5-flash-lite, pinned, no fallback (`env` in each file). A run that hits the
  quota is not counted; it is redone when the experiment resumes.
- 3 repeats per task and condition; run order randomised with a fixed seed.
- Browser repairs are given to both conditions alike.

## Success (fixed now)
A run is a verified success when all of these hold (checks v4):
1. It builds.
2. The new navbar is reachable from the app's entry (rendered), its donor code is present (copied or adapted),
   there is only one navbar, imported packages are in package.json, images exist.
3. The site's own scripts still find their elements (when the scanner found such scripts).
4. The browser check passes at 1280px and 390px: navbar visible, links not hidden on desktop, not unstyled,
   no sideways scrolling on a phone, no page errors that the original site does not already have.
A host regression: 1 and 2 hold but 3 or 4 fails.

## Analysis (fixed now)
- Per task: success rate of D minus success rate of A over the 3 repeats (paired by task).
- H1: exact sign test over tasks (D better / A better / tie), plus a paired bootstrap 95% CI of the mean
  difference. Only H1 is confirmatory; everything else is exploratory and labelled so.
- Secondary: host regressions, tokens per run, repairs until working, report adherence (from the diff),
  failure categories, and the cause of each browser problem (report advice / not in report / agent alone).
- Manual review of every pair where the automatic verdict and the browser disagree; reviews never overwrite the
  automatic verdict, both are reported.

## Changes after freezing
- **1 Nov 2026: two conditions added, before any held-out run (screening not started).** Reason: the professor's
  feedback (27 Oct) to make the integration more deterministic and cut token cost, and the pilot finding that all 5
  observed failures were mechanical (see `analysis-2026-11-01.md`). Nothing above changes: tasks, A/D conditions,
  model, success criteria (checks v4) and the H1 analysis stay as frozen.
  - **M**: deterministic port (`fusionedges/port.py`), no AI call. Deterministic, so 1 run per task, not 3.
  - **MA**: M, then the same pinned model is called only if the build or browser check fails, or a fact M could not
    resolve needs code; at most 2 calls. 3 repeats like A and D.
  - **H2 (confirmatory, fixed now):** MA's verified success rate is not lower than A's (paired by task, same sign test
    and bootstrap as H1) **and** MA uses at least 50% fewer tokens per run than A (median over all runs).
    If MA wins on tokens but loses on success, H2 fails and that is reported.
  - M runs first on all 16 candidates (no quota needed). Screening still uses agent-alone runs only, so M's
    results cannot influence which tasks are kept.
