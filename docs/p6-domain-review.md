# Independent P6 domain review

Reviewed 16 October 2026 against `amendment.py`, its tests, and the P6 requirements. This review changed no application code. P6 is a domain foundation, not an accepted amendment workflow: storage, guarded approval hashing, simulation/adoption services, API and reachable UI are still unimplemented.

## Verification

Independently ran from backend:

`python -X utf8 -m pytest tests/test_p6_amendment_domain.py tests/test_architecture_boundaries.py -q`

Result: **26 passed**, no skips. Additional pure-domain negative cases reproduced the findings below. No Mongo, SQL, API or browser amendment implementation was verified by this run.

## A. High: forward translation depends on rule order while its fingerprint does not

Create a validated CONFIRMED SPLIT mapping with source A, targets B/C and two rules:

- A occurrence 0 -> B occurrence 0.
- A occurrence 0 -> C occurrence 0.

The model accepts both rules. `translate_occurrence(..., 'A', 0)` selects the first matching rule. Reversing the rules changes the result from B:0 to C:0, while `mapping_revision` is unchanged because it sorts the rules.

This violates the intended invariant that a mapping fingerprint identifies the translation behavior used by a simulation/decision. Persistence must not build stale-preview protection on this invariant until it is fixed.

Reject ambiguous source-occurrence transfers or return an explicit unresolved result. No first-match policy, duplicating a performed fact or silent rule dropping. Test duplicate versus conflicting rules separately. For every accepted mapping, rule reordering must preserve both its fingerprint semantics and translation result. The same principle applies across mapping rows: overlapping definitions must not produce first-row-dependent outcomes.

## B. High: reverse translation of a merge guesses an earlier occurrence

Create a validated CONFIRMED MERGED mapping with sources A/B, target C:

- A occurrence 0 -> C occurrence 0.
- B occurrence 0 -> C occurrence 0.

`reverse_translate_occurrence(..., 'C', 0)` returns A:0. Reverse the rule list and it returns B:0. The fingerprint remains equal. A many-to-one relation has no unique inverse without additional reviewed/provenance information.

The function currently has no dedicated test. Before allowing reversal, reject ambiguous inverse translation or return UNRESOLVED with an actionable reason. Preserve original fact identities/lineage and V2-only facts. A later service may use actual source provenance to resolve a permitted case, but it cannot pretend that an arbitrary first row is that provenance. A patient returning to V1 is a newly authorized evaluated operation, not generic reversal of a mapping table.

Test unambiguous equivalent/renamed pairs, explicit split/merge rules, ambiguity, added/removed events, rejected/proposed rows, missing rules and multiple mapping rows. Round-trip identity is appropriate only where a unique reversible pairing actually exists.

## C. Mapping coverage is not currently a review-readiness gate

An event-code reuse with a changed name produces PROPOSED mapping status as intended. However, `mapping_coverage_problems` returns an empty list for that complete but unreviewed mapping. The docstring says review readiness is checked, while the implementation does not report unresolved review state. An existing test pins the empty result.

Choose an explicit contract: either include review blockers in this function or keep structural coverage separate from a required readiness validator and fix the naming/docstring/call sites. Adoption/approval/simulation must use the appropriate complete gate. Translation already refuses proposed rows; do not weaken that safeguard.

Also ensure resolved split/merge mappings always need sufficient rules, regardless of how a status was supplied. Do not let AUTO_CONFIRMED, a client-provided reviewer UUID or a free-text provenance string establish trusted review. Future API/service code must derive server-owned automatic matches and authenticated review decisions.

## D. Hash and validation tests need stronger isolation

The current date-affecting hash test constructs another schedule and changes some IDs back. It still has fresh identifiers/metadata, so hash inequality alone does not prove the timing change caused the difference. Deep-copy one schedule and change one field at a time for meaningful regressions across timing, actual basis, windows, recurrence, dependencies, tracks, anchors, population/assignment rules and evidence.

Excluding lifecycle status and creation time from content identity is reasonable. `effective_from` exclusion needs an explicit separate lifecycle/adoption integrity contract: content hash alone cannot prove an excluded field is unchanged. Bind patient simulations/decisions to the effective/adoption point and required metadata as separate material inputs; enforce immutable parent/version/approval references. Do not claim the content hash protects every header field.

The new header content_hash field is only a field today. Store/validate its algorithm version and compute/persist it at the guarded approval boundary. Account for historical approved headers without a hash without silently rewriting their clinical content. This review did not find or verify a storage implementation for that promise.

## Continuation

Repair/test A-C before persisting these mapping contracts. Then complete P6-01 through P6-05, including real-Mongo transaction/concurrency proof and production-route UI reachability, using the saved P6 continuation prompt and the P6-P14 master. Preserve historical actual facts and the selected authority/policy boundaries. Do not mark any of the five P6 acceptance rows complete from these 26 domain tests alone.

## Resolution (16 October 2026, P6 completion slice)

Each finding above was reproduced first, then fixed and pinned by a regression; the full application workflow was built on the repaired contracts. Details: [p6-completion-record.md](p6-completion-record.md).

| Finding | Repair | Regression |
|---|---|---|
| A. forward translation depended on rule order under one fingerprint | a source occurrence sent to two visits is rejected at validation; exact repeats collapse; rules are stored in canonical order; translation returns a unique destination or `UNRESOLVED`; two rows naming one visit are ambiguous, never first-row | `tests/test_p6_mapping_repair.py` (`test_a_source_occurrence_cannot_be_sent_to_two_visits`, `test_exact_duplicate_rules_collapse_and_rule_order_changes_nothing`, `test_overlapping_rows_are_ambiguous_not_first_row`) |
| B. reverse translation of a merge guessed an earlier occurrence | a many-to-one relation has no unique inverse from the mapping alone: `UNRESOLVED` with an actionable reason; a visit with nothing recorded on it simply has no counterpart; a return is blocked where a fact has no unique origin | `test_merge_inverse_is_ambiguous_not_first_row`, `test_unambiguous_reverse_round_trips`, `test_reverse_covers_every_mapping_type_and_status`, and the service-level split/return cases in `tests/test_p6_adoption_cases.py` |
| C. coverage was not a review-readiness gate | structural coverage (`mapping_coverage_problems`) and readiness (`mapping_readiness_problems`) are separate; `mapping_problems` returns both; the service gate adds provenance (an `AUTO_CONFIRMED` row must be re-derivable from the two snapshots, a reviewed row needs an authenticated decision, the stored fingerprint must match); `AUTO_CONFIRMED` is refused for splits/merges | `test_readiness_is_separate_from_structural_coverage_and_combined_gate_reports_both`, `test_automatic_confirmation_cannot_stand_in_for_review`, `test_a_tampered_stored_mapping_set_fails_the_gate` |
| D. hash tests and the header contract | single-field mutations on one deep-copied schedule; a separate `header_integrity_hash` covers `effective_from`, lineage and approver; algorithm version stored; stamped in the guarded approval write; hashless historical versions reported `UNVERIFIED_LEGACY` | `tests/test_p6_content_hash.py`, `tests/test_p6_approval_hash.py` |
