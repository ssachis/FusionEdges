# Evidence ledger

Every FusionEdges run belongs to one batch below. **Only batches marked _study_ may support a result.** The rest is the record of how FusionEdges was built, and is cited as such. Labels live in `experiments/evidence-ledger.json`; regenerate this file with `python3 -m fusionedges evidence`.

| Status | Meaning |
|---|---|
| **exploration** | Trying things out. Mixed models, failed AI calls, one task. Not evidence. |
| **pilot** | First structured comparison. Too small and not pinned to one model. Shapes the questions, answers none. |
| **development** | Runs used to build and fix FusionEdges. The rules were changed after seeing these results, so they cannot test it. |
| **preliminary** | A fair comparison on tasks that were also used for development. Shows direction, not proof. |
| **study** | Frozen pipeline, fresh tasks, analysis fixed in advance. The only batches a result may come from. |

| Batch | Status | Runs | Tasks | Approaches | Model(s) | What it is |
|---|---|---|---|---|---|---|
| Early runs (15-18 Oct) | exploration | 23 | 1 | A, D | gemini-3.5-flash-lite, gemini-3.6-flash, groq:openai/gpt-oss-120b | One navbar task, prompt vs report, four different models, several failed AI calls. |
| Pilot (27 Oct) | pilot | 12 | 4 | A, D, M1 | gemini-3.5-flash-lite, gemini-3.6-flash | 4 navbar tasks, agent alone vs agent + report, then the first port runs. Models mixed after a quota fallback. |
| Port v1 (1 Nov) | development | 16 | 16 | M1 | no AI | First rule-based port on 16 tasks, no browser check. |
| Port v2 (1 Nov) | development | 20 | 20 | M2 | no AI | Rules added after v1's failures; first browser check. |
| Preliminary navbar comparison (2 Nov) | preliminary | 52 | 20 | A, M | gemini-3.5-flash-lite | 16 navbar tasks: agent alone (2 runs each, gemini-3.5-flash-lite) vs port v3 (no AI), with a side-by-side review. Fair setup, but the port's rules were written while looking at these tasks. |
| Adaptive pilot v1 (2 Nov) | development | 11 | 4 | A, AD | gemini-3.6-flash | 7 of 32 runs across navbar, footer, card, accordion; adaptive runs crashed on a Gemini tool-call bug. |
| Six-run diagnostic attempts (2 Nov evening) | development | 3 | 1 | AD | gemini-3.6-flash | Card, accordion, footer on two familiar hosts; stopped by the gemini-3.6-flash free quota. |
| Study 1: navbar, Next.js donor into a React site | study | 0 | 0 | - | - | Frozen pipeline (port v4 + adaptive), ~30 fresh tasks, agent alone vs FusionEdges, 2 runs each, gemini-3.5-flash-lite, blind review of a sample. See experiments/study-1/PLAN.md. |

Approach letters: A agent alone · D agent + AI-written report · M FusionEdges port, no AI (M1, M2 earlier versions) · AD adaptive FusionEdges (rules first, report and AI only as needed).
