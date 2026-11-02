# Seven-run pilot review — 2 November 2026

Seven of the planned 32 runs are recorded: four adaptive and three prompt-only runs. All seven built. One passed the existing automatic checks (prompt-only card). This is an incomplete, unbalanced batch, not evidence that either approach wins.

All four adaptive runs stopped with Gemini's missing `thought_signature` error. The client discarded provider metadata when replaying tool calls. Their token accounting can also omit successful calls in the failed agent stage. Do not publish comparative token savings or success rates from this batch.

## Qualitative review

Desktop and mobile screenshots and code diffs were inspected by the coding assistant. This review is unblinded and is not independent human evaluation.

| Run | Observation |
| --- | --- |
| Adaptive navbar | Component present, but requested branding/links wrong and links run together. Original page retained. Agent stopped on provider error. |
| Prompt navbar | Original page unchanged; donor navbar absent. |
| Adaptive footer | Requested content present, but spacing/color fidelity incomplete. Original page retained. Tall-footer warning was a checker false positive; genuine styling issues remain. Agent stopped on provider error. |
| Prompt footer | No donor footer delivered; original page retained. |
| Adaptive card | Three donor-like cards replaced the original cards instead of adding one; original content lost. Agent stopped on provider error. |
| Prompt card | Automatic pass, but link styling differs and unrelated hero text was shortened/replaced. Preservation checks missed this regression. |
| Adaptive accordion | Accordion absent from the rendered page. Original page retained. Agent stopped on provider error. |

None is fully correct under this product-quality review. That does not overwrite the original automatic scores. Raw records, screenshots and diffs are retained beside this report.

## Disposition

The exact v1 code, manifest and progress are archived under `experiments/frozen/adaptive-pilot-v1`. The existing pilot launcher imports that archive, so merging the GitHub UI does not silently change v1's implementation.

The active code now preserves Gemini tool-call signatures and raw arguments for the same provider/model, retains recorded agent usage on provider errors, and removes navbar-only link/height assumptions from footer checks. These changes need a newly frozen benchmark version before collecting corrected comparative evidence. Keep the seven v1 runs as diagnostics, separate from that comparison; strengthen original-page text preservation checks for the next version.

GitHub project selection, review and approval-gated draft PR creation were merged locally. No live GitHub PR was opened and no Gemini retry was made during this review. GitHub App credentials still require local setup.
