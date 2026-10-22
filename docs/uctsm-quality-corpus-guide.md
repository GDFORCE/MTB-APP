# UCTSM quality corpus and gate

Status: engineering tooling, 19 October 2026. The checked-in ten-case corpus is synthetic. Its predictions are deterministic engineering fixtures, not outputs of a protocol-reading model, and a passing engineering run is not a measured extraction-accuracy result or clinical release approval.

## Run locally

From `backend/`, with the project's Python 3.11 environment:

```powershell
python -m pytest -q tests/test_pdf_quality.py tests/test_pdf_graph_integrity.py tests/test_architecture_boundaries.py
python -m app.quality --corpus quality_corpus/manifest.json --predictions quality_corpus/predictions.json --thresholds quality_corpus/thresholds.json --source-root .. --mode engineering --output D:/mtb_tmp/uctsm-quality/report.json
```

The isolated CI dependency list is `backend/quality-requirements.txt`. Install it into a separate virtual environment when checking that the gate does not depend on the API, MongoDB or provider libraries. Do not install over an unrelated application's environment.

Run `python -m app.quality.engineering_corpus` to regenerate the engineering fixtures, then inspect `git diff -- quality_corpus` from `backend/`. Identical generated content should produce no diff. Source files use LF through `.gitattributes`; source hashes refer to their actual bytes. Do not rewrite expected dates from evaluator outputs to make a failing oracle pass.

The CLI defaults to `--mode clinical`. Exit 0 means that the selected mode passed; exit 1 means failed evidence/quality gates; exit 2 means invalid or unreadable input. Inspect `clinical_release_qualified`, `reason_codes`, explicit scoring denominators, categorized field failures, execution failures and unsafe-date counts in the report. Input errors expose only error types in logs. Corpus, predictions, thresholds and trusted-reviewer JSON files are individually limited to 32 MiB.

## Data and signature contracts

`app.quality.models` owns the strict `uctsm-quality.v1` schema. Unknown fields, duplicate identities, duplicate expected rule/path keys, duplicate scenario IDs, version-mismatched contexts and gold/source-manifest mismatches are rejected.

Every source states its identity/version, relative path, SHA-256, provenance, license and verification state, de-identification and synthetic status, and construct tags. Source paths must resolve within `--source-root`; hashes must match actual bytes. Ten construct families are currently required: linear, cycle, actual-relative, branch, multi-track, recurrence, device, amendment, conflict and unsupported. A tag alone does not prove clinical representativeness.

An expected field has a stable rule identity/path and an exact typed value. Only explicitly selected unordered string collections use that equivalence; duplicate members still count. Booleans and integers are distinct. Scenarios specify fixed as-of/horizon dates, canonical schedule/context and exact logical-occurrence targets, windows and statuses, or exact blocking validation codes. Unexpected generated targets fail safety checks. The runner invokes the domain evaluator and now also has an application-service sequence runner. The current synthetic command sequence checks immutable history and changed/unchanged dates. Amendment adoption, reversal, pause/resume and broader sequence coverage remain unfinished P8-04 work.

Clinical qualification additionally requires genuine eligible clinical records, verified data controls, independent review and approved thresholds. Do not relabel the engineering records, construct signatures for another person, treat public availability as a verified license, or promote the official identity-test PDF to signed clinical gold.

Pass a trusted-reviewer JSON object via `--trusted-reviewers`. Each key is an approved `key_id`; its value has `reviewer_id` (UUID) and `public_key` (base64 raw Ed25519 public-key bytes). This is the verifier's independently controlled trust list. A key embedded by the submitted gold author must not become trusted automatically. Private keys belong outside the repository and CI logs.

`GoldRecord.content_hash()` hashes canonical JSON excluding `signoff`; `Thresholds.content_hash()` excludes `approval`. Source-manifest hashes bind document version, provenance, license and data controls. The reviewer signs the exact bytes returned by `app.quality.runner.approval_message(signoff)`: canonical JSON of reviewer UUID, key ID, timezone-aware review timestamp and reviewed content hash, excluding the signature. Store the base64 signature in the record. Any signed-content change needs review again. Unknown keys, mismatched reviewers/hashes, missing or future/naive timestamps and invalid signatures fail verification.

## CI and remaining acceptance

`.github/workflows/uctsm-quality.yml` runs the offline engineering gate on changes and retains reports. Its manual clinical mode deliberately fails on the current unsigned engineering corpus. It is not a deployment workflow. A remote CI run, approved clinical corpus/thresholds, model-generated predictions and versioned comparison/trend evidence are still required. Do not bypass a clinical failure by switching a release gate to engineering mode.
