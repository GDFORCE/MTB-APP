# MTB protocol extraction lab

A separate local Streamlit app for testing the **existing** protocol reader,
reconciler and canonical schedule compiler with Gemini schema-constrained JSON.
It does not import `server.py`, approve a schedule, enroll patients, access a
database or change the production extraction provider.

## Start

From the MTB-APP repository root, using Python 3.11 or newer:

```powershell
python -m pip install -r prototypes/extraction_lab/requirements.txt
python -m streamlit run prototypes/extraction_lab/app.py --server.address 127.0.0.1 --server.port 8502 --browser.gatherUsageStats false
```

Open **http://localhost:8502**. You can also run
`powershell -File prototypes/extraction_lab/run.ps1`.

For an isolated environment, first create and activate
`prototypes/extraction_lab/.venv`, then install the requirements there. No MongoDB
or PostgreSQL instance is needed.

## Test without a key

1. Select **Offline fixture replay** in the sidebar.
2. Run a built-in document, or open **Case suite** and select cases.
3. Inspect schedule definitions, facts/citations, blockers and expected checks.

The 23 synthetic cases replay supplied expected answers through the real
reader/reconciler/compiler. This tests pipeline integration, not Gemini accuracy.
Some tests should produce unresolved/blocking findings: refusing to guess is the
expected behavior for those cases.

CLI equivalent, with no external API calls:

```powershell
python -m prototypes.extraction_lab.core --offline-suite --output .tmp/extraction-lab/offline-report.json
```

## Test Gemini

1. Create a key for a free-tier project in [Google AI Studio](https://aistudio.google.com/apikey).
2. Select **Gemini API** and enter the key in the sidebar's password field.
3. Select a model available to that project. The listed choices were checked
   against official documentation on 22 October 2026: `gemini-3.8-flash`,
   `gemini-3.6-flash` and `gemini-3.5-flash-lite`. A custom ID is also accepted.
4. Start with **one built-in synthetic case** and **Schema-constrained JSON**.
5. Inspect expected-field checks, citations, compiled rules and request records.
6. Compare the same case using **JSON-only baseline**. These caches are separate.
7. Then try a permitted PDF/text document. Add reviewer-authored expectations to
   measure selected fields; an upload without expectations is **NOT_SCORED**.

Alternatively copy `.env.example` to `.env` inside this prototype and set
`GEMINI_API_KEY`. That local file is ignored. The app never loads `backend/.env`.
Do not paste keys into chat or commit them. Raw replies and run results stay in
the local Streamlit session until explicitly downloaded. Clearing cache removes
the lab's replies/results from that session.

Google lists free-tier access for these Flash choices, but eligibility, capacity
and quotas depend on your project. The app cannot inspect billing or guarantee a
paid-project key incurs no charges. It does not enable billing. When the chosen
model returns an availability error (503, 429, 500, 504, timeout, model not
found), the app moves to the next model in the order shown in the sidebar and
stays on it for the rest of the run; each attempt is recorded and counts toward
the request cap. Key, schema and content errors never trigger a switch. Turn the
switch off in the sidebar to stay on one model. Start with a free-tier project
and a small cap.
See [pricing](https://ai.google.dev/gemini-api/docs/pricing) and
[your active rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

Google's free-tier pricing documentation says content may be used to improve
its products. Use synthetic/public documents or documents you have permission to
send under those terms. The built-in suite contains only project-authored
synthetic text.

## What is constrained

The injected Gemini adapter sets `response_mime_type="application/json"` and
`response_json_schema=<stage schema>` on `GenerateContentConfig`. Separate
schemas cover the real reader's metadata, facts/table/footnote and malformed-fact
repair questions. Schema selection recognizes those explicit question prefixes;
an unknown future question fails before sending a request.

The schemas derive from the backend's fact models, retain nullable missing
values and aliases, close known objects and exclude model-authored deterministic
provenance/citation verification. Replies are validated locally with JSON Schema;
the original reader then applies Pydantic validation, citation verification,
coverage accounting, reconciliation and compilation. There is no fallback to
unconstrained output if the provider rejects a schema.

The JSON-only baseline requests JSON MIME type without a response schema. Local
schema failures are recorded, while the real reader handles any usable reply.

This is provider-side structured generation, not a locally implemented token
masking engine. Schema adherence does not establish that clinical values are
correct. Google's [structured output guide](https://ai.google.dev/gemini-api/docs/structured-output)
also calls for application validation of the returned values. The
[GenerateContent API reference](https://ai.google.dev/api/generate-content#v1beta.GenerationConfig)
documents the response schema configuration.

## Limits, coverage and reuse

- A run has an explicit new-request cap; a suite shares that cap across cases.
- One SDK attempt is allowed per recorded request. The real reader still has
  its own bounded malformed-reply/fact repair behavior, which consumes the cap.
- Request pacing is adjustable; default is 12 seconds between new requests.
- Quota failures stop subsequent suite cases; they are recorded **NOT_RUN**.
- Provider errors, truncated replies and local schema failures remain visible.
- Page/call limits do not silently turn partial reading into a complete result.
- Successful reader answers are reused in memory by exact document/question/
  model/mode/schema identity. Completed work can survive retry within the session.
- Changing output mode or schema separates the cache. To make repeated stochastic
  model calls, clear the cache first. A/B runs alone are not statistical evidence.
- PDFs are sent as scoped native page packets through the existing client packet
  logic. Scans may yield unverified evidence, which remains visible to review.

## Expectations for your own documents

Download a built-in case's expectations as a template. Upload a reviewed JSON
file alongside your source. For example:

```json
{
  "checks": [
    {
      "name": "Screening labs",
      "kind": "visit",
      "aliases": [],
      "fields": {
        "timing.pattern": "WITHIN_BEFORE",
        "timing.from.name": "Registration",
        "timing.duration": {"value": 28, "unit": "DAY"},
        "window": null
      }
    }
  ],
  "required_issue_codes": [],
  "forbidden_visit_names": [],
  "minimum_table_cells": 0,
  "required_footnote_labels": [],
  "expected_schedule_options": 1
}
```

Values must come from the uploaded source and reviewer decisions. Dotted paths
address normalized facts; numeric segments address array positions. Checks use
typed exact equality. A fact cannot satisfy two expected occurrences. Required
missing values can be checked as `null` instead of assigning guessed defaults.

Scores measure only the specified fields/presence/absence checks. They do not
measure all possible extra hallucinations or establish clinical approval. No
expectations means no accuracy claim; incomplete coverage prevents a PASS even
when all selected fields happen to match. Use diverse independent real protocols
and reviewer-authored expectations before drawing broader conclusions.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI, uploads, case selection and reports. |
| `client.py` | Constrained/baseline Gemini adapter, request budgets and offline client. |
| `schemas.py` | Stage response schemas derived from backend fact models. |
| `core.py` | Document handling and invocation of the real extraction runner. |
| `cases.py` | 23 synthetic source documents and replay fixtures. |
| `gold.py` | Explicit reviewer expectation validation and scoring. |
| `tests/test_lab.py` | Pipeline, provider-boundary, scoring and UI tests with no real API key. |

Run the lab tests from the repository root:

```powershell
python -m pytest -q prototypes/extraction_lab/tests
```

Live Gemini accuracy is not established by offline tests. No key was present in
the process environment during initial implementation, so live provider runs
must be performed using your own project key in the UI.
