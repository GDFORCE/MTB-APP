# P6 independent review evidence

`reproduce_findings.py` is the **historical** reproduction of defects F1–F4 as observed on
2026-10-16. Its assertions document the *defective* behaviour, so it now fails against the
repaired code by design; do not use it as an acceptance gate.

The safe-behaviour regressions that replaced it are in
`backend/tests/test_p6_repairs.py` (memory fake and real MongoDB):

| Finding | Regression coverage |
| --- | --- |
| F1 cross-trial lineage/pair writes | `test_a_lineage_request_cannot_write_another_trials_versions`, `test_a_pair_request_cannot_write_another_trials_comparison_even_on_replay`, `test_the_service_refuses_versions_of_a_different_trial_before_any_write` (response, no lineage/pair/mapping/audit change) |
| F2 preview vs committed calendar | `test_a_deferred_preview_shows_what_the_commit_persists`, `test_every_previewed_target_equals_the_committed_target`, provisional + baseline tests |
| F3 trigger withdrawn after claim | `test_a_trigger_withdrawn_after_the_claim_does_not_apply_the_adoption`, re-occurrence test, `input_revision` write-skew guard tests |
| F4 worker starvation | batch-of-one, >100 waiting, multi-organization, held/expired lease and provider-error rotation tests |
