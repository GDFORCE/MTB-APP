# Runbook: patient evaluation latency

**Alert:** `UctsmEvaluateLatencyHigh` · **Owner:** platform on-call

1. Check `uctsm_workflow_seconds{stage="patient_evaluate"}` and whether load or one schedule is the cause.
2. Run the context-query explain test (`tests/test_pdf_context_queries.py`) against a copy of the data to confirm the indexes from migration 0014 exist (`db.<collection>.getIndexes()`); a missing index is the usual cause.
3. Compare the recurrence safety cap and horizon of the slow schedule; incomplete projections are reported, not hidden.
4. Never raise caps in production as a mitigation without an approved change.
