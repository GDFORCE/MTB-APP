# One question at a time — three interactive designs

Current focused design for the protocol-question screen shown in the user's screenshot. Prepared 23 October 2026. **No application code changed.**

Open [the interactive previews](preview.html) locally in a browser. No server, account, upload or API is required.

1. [Focus](preview.html#focus): a dedicated screen, generous spacing, direct answer choices. Recommended layout.
2. [Question card](preview.html#card): a single card on a quiet background, retaining clinical blue.
3. [Compact sheet](preview.html#sheet): preserves an overlay while replacing the long scrollable question list with individual steps.

[Compare all three visually](three-design-options.png).

## Interaction

- Exactly one question per design is visible at a time.
- Tap an answer to select it and advance to the next question. A short 260 ms transition makes the selection visible; reduced-motion users advance without animation.
- Back restores the earlier question and its answer. Every answer remains changeable.
- The original longer question, explanation and source reference remain under “Why am I being asked?”
- No answer is preselected. A suggested choice remains distinguishable from an explicitly accepted answer.
- After both questions, a short review allows either answer to be changed. Changing an answer from review returns directly to review, preserving the other answer.
- The existing final “Confirm and continue” action remains. This preview ends with a local confirmation state and does not apply an answer to any trial or backend.
- Each design keeps an independent in-memory preview. Restart clears only the local demo. No localStorage, cookies, network calls or app session are used.

## Preserve the real functionality

The current implementation is `frontend/src/features/kit/KitQuestionsModal.tsx`. It receives model-driven review questions and study-scoped site questions, can apply changed review answers through `patchKitModel`, and calls `onConfirm` with the confirmed model and answer maps. The current `onDismiss` availability is conditional; this design does not authorize making a mandatory review dismissible.

Any future implementation should render the actual model's question count, identifiers, labels, options, help, recommendations and required flags. Keep study-scoped site settings distinct from individual participant questions. Preserve errors, retry, disabled/busy states, per-model required-question checks and the final confirmation/apply operation. The sequential layout must not invent visit windows, anchors, recurrence rules, choices or clinical policies.

The prototype uses the first question and proposed window choices visible in the supplied screenshot. Those values are not endorsed protocol rules. The second question is based on the visible EDTA question, but its answer wording is illustrative because the screenshot does not show its actual options. Actual options must come from the reviewed protocol model. The full text and this distinction are accessible in the expanded detail.

The first question has been shortened for display, with the original retained in the disclosure. That is proposed interface copy, not a replacement for the protocol meaning or evidence.

## Verification

See [verification.json](verification.json) for browser checks. The previews were checked in Chrome at desktop and narrow widths, including answer advancement, Back, disclosure, independent state, answer editing, final confirmation and reset. Only local design files were loaded.

The broader screen/option audit remains in [the detailed review folder](../mtb-ui-detailed-2026-10-23/README.md); this standalone design addresses the latest request to simplify the protocol-question flow. Earlier visual concepts are preserved.
