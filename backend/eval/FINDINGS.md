# Protocol → visit-schedule extraction — validation findings

Live-tested via `eval/run_eval.py` against `claude-opus-4-8` with the hardened
system prompt in `protocol_extraction.py`. Each archetype is a synthetic
Schedule-of-Assessments PDF; the extractor returns the flat
`{name, day_offset, window_days, activities}` list the frontend consumes.

## Result per archetype

| Archetype | Verdict | Notes |
|---|---|---|
| Normal / single-arm linear | ✅ Pass | Screen D-14 → −14, Baseline → 0, Wk4/8/12 → 28/56/84; windows ±3/±7; activities per marked cell. |
| Cyclic / oncology (3×21d) | ✅ Pass | 6 visits enumerated across all cycles with **absolute** days via (C−1)·L+(D−1): C1D1→0, C1D8→7, C2D1→21, C2D8→28, C3D1→42, C3D8→49. Named "Cycle C Day D". Tumor scan only on C1D1/C3D1. |
| Parallel 2-arm, shared SoA | ✅ Pass | Recognized the single shared schedule — emitted each visit **once**, not duplicated per arm. |
| Multi-arm, divergent schedules | ✅ Pass | Split per arm with "Arm A — …" / "Arm B — …" name prefixes; Arm A weekly (7/14/28), Arm B monthly (30/60); MRI on the correct visits. |

## How structure is represented (design)

The output stays a **flat** list (the frontend contract is unchanged). Cycle /
arm / period structure is encoded by (a) an **absolute** `day_offset` on every
row and (b) a **self-describing name** ("Cycle 2 Day 1", "Arm B — Week 4",
"Period 2 Day 1"). No schema change was needed — so the visit-schedule editor
just works and shows meaningful rows the sponsor reviews before saving.

## Remaining limitations / recommendations

- **Very large cyclic protocols** (e.g. 24 cycles) produce many rows; the model
  expands what the protocol defines. If a protocol is open-ended ("continue until
  progression"), it expands the stated maximum — verify long schedules before
  saving.
- **Truly divergent multi-arm** relies on the model reading arm structure from
  the table; extremely unusual layouts may need a human pass. The
  review-before-save flow is the safety net — extraction is a **draft**, never
  auto-saved.
- If arm/cycle grouping is ever needed downstream (not just display), add
  optional `arm`/`cycle` fields to `ExtractedVisit` (backward-compatible) rather
  than parsing them back out of the name.

## Reproduce

```
cd backend
./.venv/Scripts/python.exe -m pip install fpdf2
./.venv/Scripts/python.exe eval/run_eval.py   # needs ANTHROPIC_API_KEY in .env
```
