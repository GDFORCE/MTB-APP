# Next prompt to paste into Claude Code

Updated 16 October 2026 after the independent P6 end-to-end review. **Repair P6 F1-F4 and triage the existing integration failures first; then continue P7/P8.** All four defects reproduced on memory and real local Mongo despite the existing passing tests.

```text
Read docs/claude-code-p6-audit-repairs-before-p7-p8-prompt.md completely
and execute it as the current focused continuation under the P6-P14 master.

Start with docs/p6-end-to-end-independent-review.md and its saved reproduction.
Repair cross-trial writes, deferred preview/commit disagreement, adoption after
its trigger was withdrawn between claim and apply, and worker starvation.
Prove the repairs on memory and real Mongo; triage the known integration-suite
failures without weakening authority, validation or the selected policies.

Then continue docs/claude-code-p7-p8-after-p6-prompt.md. Preserve all prior work,
honest unsupported/external gates and prompt history. No commit, push,
deployment, real-patient migration or live read-mode/authority changes.
```

[Current full repair prompt](claude-code-p6-audit-repairs-before-p7-p8-prompt.md).

[Independent findings and evidence](p6-end-to-end-independent-review.md).

[Queued P7/P8 continuation](claude-code-p7-p8-after-p6-prompt.md).

[Remaining-PDF master](claude-code-p6-p14-remaining-pdf-prompt.md).

[Preserved previous launcher](claude-code-p7-p8-launcher-2026-10-16.md) and [all saved prompts](claude-code-prompts-index.md).
