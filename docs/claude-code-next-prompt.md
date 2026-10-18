# Next prompt to paste into Claude Code

Updated 16 October 2026 at the owner's request. **One combined prompt now includes the P6 repairs, integration-failure triage, all five P7 tasks and all five P8 tasks.** Execute the combined file; the earlier standalone repair prompt is historical.

```text
Read docs/claude-code-p7-p8-after-p6-prompt.md completely and execute it
as the single current focused continuation under the P6-P14 master.

It includes the four P6 defects and their regression requirements:
trial/body scope, reconciled preview versus commit, withdrawn adoption
triggers and fair deferred-worker progress. Repair these and triage the
known integration failures, then complete P7-01 through P7-05 and
P8-01 through P8-05 with real-store, API/UI, scoring and gate evidence.

Think through shared invariants and dependencies before implementing.
Preserve all prior work and selected policy. Distinguish engineering
fixtures from signed clinical gold and report exact acceptance gaps.
No commit, push, deploy, live migration or read-mode/authority change.
```

[Full combined prompt](claude-code-p7-p8-after-p6-prompt.md).

[Independent P6 findings](p6-end-to-end-independent-review.md) and [remaining-PDF master](claude-code-p6-p14-remaining-pdf-prompt.md).

[Earlier short P7/P8 prompt](claude-code-p7-p8-before-merged-repairs-2026-10-16.md), [earlier repair launcher](claude-code-p6-repair-launcher-2026-10-16.md) and [prompt index](claude-code-prompts-index.md) preserve history.
