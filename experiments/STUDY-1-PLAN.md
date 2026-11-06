# Study 1 — navbar transfer, Next.js → React

**Question.** Does FusionEdges get a working navbar into an existing React site more often, for fewer AI tokens and less time, than a plain AI agent?

## What is compared
| Arm | What runs | AI budget |
|---|---|---|
| Agent alone (`prompt`) | One AI coding call, then up to 2 repairs | Whatever it uses |
| FusionEdges (`adaptive`) | Rules port → verify → compatibility report → 1 local AI edit → bounded agent (8 steps), stopping at the first stage that passes | Often 0 |

This compares two whole systems. It is not an equal-call-budget test of the report on its own.

## Tasks
- 15 React hosts (Vite or Create React App) × 2 Next.js donors = **30 tasks**.
- **Fresh tasks only.** None of these repos was used while writing the port rules. They were found by a fixed GitHub search (pushed Oct–Nov 2026), and the pairing was drawn with seed 261103.
- **Pinned.** Every repo is pinned to a full commit, and every donor navbar file to a sha256.
- **2 repeats per arm** → 120 runs. Runs follow a randomised order that is the same on every resume.

## Frozen acceptance contract (same in both arms)
- **Wrapper:** the navbar must sit inside exactly one `[data-fe-transfer="study1-navbar"]` element.
- **Host preserved:** 1–2 headings or texts from the host's own pages must still be there, exactly once and visible.
- **Automatic checks:** it builds; the donor's code is in the result; the navbar renders at 1280px and 390px; there is one navbar on the page; images exist; packages are declared; React major version and framework are unchanged.

## Measures
- **Primary:** per task, the share of runs that pass every check *and* the contract (`strict_success`). Each task counts once, and the two arms are compared with a sign test over tasks.
- **Cost:** AI tokens per run and per working result (all tokens spent ÷ runs that work), plus wall time.
- **Quality:** a blinded side-by-side review of screenshots and diffs, asking whether the donor navbar is recognisable and the host is intact.
- **Overall result:** the checks AND the review.

## Controls
- **Model:** one pinned model, `gemini-3.5-flash-lite`, free tier, with no fallback.
- **Memory:** off in both arms. Memory/RAG is a later, separate study.
- **Frozen code:** `code_sha256` is set at freeze. Any code change after that is refused by the strict lane and needs a new experiment.
- **Readiness:** before freezing, every host must build, render, and show its preserved text once (`experiments/study1_ready.py`, no AI). Tasks that fail are replaced, never patched mid-study.
- **Quota stops:** a run that hits the quota is redone later, and its tokens still count.

## Reporting
- **Every result is shown:** all cells, failures, and quota retries.
- **Analysis tab:** shows this batch as **study** (ledger id `study-1`).
- **Repeats are not independent samples**, so no claim is made beyond these 30 tasks.
