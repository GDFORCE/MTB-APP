"""Run: python -m streamlit run prototypes/extraction_lab/app.py"""
from datetime import date, timedelta
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from dotenv import load_dotenv

from prototypes.extraction_lab import bootstrap  # noqa: F401
from app.extraction.artifacts import MemoryArtifactStore
from prototypes.extraction_lab.cases import CASES, BY_ID
from prototypes.extraction_lab.client import DEFAULT_MODEL, MODEL_CHOICES, RequestBudget, StructuredGeminiClient, fallback_chain
from prototypes.extraction_lab.core import event_rows, extract, reader_limits, replay
from prototypes.extraction_lab import engine
from prototypes.extraction_lab.gold import Expectations
from prototypes.extraction_lab.schemas import response_schema

DEFAULT_WINDOW = 8

# Read only this prototype's optional .env; never load the production .env.
load_dotenv(Path(__file__).with_name(".env"), override=False)

st.set_page_config(page_title="MTB Extraction Lab", page_icon="🧪", layout="wide")
st.title("Protocol extraction lab")
st.caption("Protocol pages → cited facts → canonical schedule → review findings. Creates no approved schedules or patient records.")

if "lab_artifacts" not in st.session_state:
    st.session_state.lab_artifacts = MemoryArtifactStore()
st.session_state.setdefault("window", DEFAULT_WINDOW)

with st.sidebar:
    st.header("Settings")
    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        api_key = env_key
        st.caption("✓ API key loaded from .env")
    else:
        api_key = st.text_input("Gemini API key", type="password")
    default_model = os.getenv("GEMINI_MODEL", "").strip().removeprefix("models/") or DEFAULT_MODEL
    options = list(dict.fromkeys(MODEL_CHOICES + [default_model]))
    model = st.selectbox("Model", options, index=options.index(default_model))
    auto_switch = st.toggle("Switch model automatically if unavailable", value=True)
    chain = fallback_chain(model) if auto_switch else [model]
    st.caption("Order: " + " → ".join(chain))
    # The "window" is how many pages go to the model in one request; the +/- buttons step it by one.
    window = int(st.number_input("Pages per window", 1, 12, key="window", step=1,
                                 help="Pages sent per request. Larger windows need fewer requests."))
    with st.expander("Advanced"):
        mode = st.radio("Run mode", ["Gemini API", "Offline fixture replay"], key="run_mode")
        constrained = st.radio("Output mode", ["Schema-constrained JSON", "JSON-only baseline"]) == "Schema-constrained JSON"
        max_calls = int(st.number_input("Maximum API requests per run", 2, 200, 40))
        interval = float(st.number_input("Seconds between requests", 0.0, 60.0, 6.0, step=1.0))
        max_pages = int(st.number_input("Maximum pages to read", 1, 400, 200))
        st.caption("Free quotas vary by account; the app cannot inspect billing. A model switch uses the same key and project.")
        st.link_button("Get a key / inspect quotas", "https://aistudio.google.com/apikey")
    if st.button("Clear cached results"):
        for name in ("lab_report", "lab_suite"):
            st.session_state.pop(name, None)
        st.session_state.lab_artifacts = MemoryArtifactStore()
        st.rerun()

live = mode == "Gemini API"
ready = bool(api_key.strip() and model.strip()) if live else True
batch_size = window
limits = reader_limits(max_pages=max_pages, max_calls=max_calls, pages_per_batch=batch_size,
                       time_seconds=max(600, max_calls * (interval + 120)))


def client_for(budget):
    return StructuredGeminiClient(api_key=api_key, model=model, budget=budget, constrained=constrained,
                                  fallback_models=chain[1:])


def display_report(report):
    st.caption(f"Recorded run: {report['mode']} · {report['model']} · {report['filename']} · {report['document_hash'][:12]} · {report['seconds']}s")
    if report["mode"] == "OFFLINE_FIXTURE_REPLAY":
        st.warning("Offline fixture replay: no LLM was called. These results test the pipeline using supplied expected replies.")
    if report.get("error"):
        st.error(report["error"])
    cols = st.columns(4)
    cols[0].metric("Source coverage", report["coverage_status"])
    cols[1].metric("Visits compiled", report.get("event_count", 0))
    cols[2].metric("Blocking findings", report.get("blocking_issues", 0))
    score = report["score"]
    cols[3].metric("Expected-field checks", f"{score['passed']}/{score['total']}" if score["total"] else "Not scored")
    st.caption(f"Check result: {score['status']}. {score['note']}")
    visits, facts, findings, coverage, expected, requests, raw = st.tabs(
        ["Schedule", "Facts & citations", "Review findings", "Coverage", "Expected checks", "Model calls", "JSON report"])
    with visits:
        rows = event_rows(report)
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("No patient visit definitions were compiled. Review source, facts and findings; reporting-only cases can legitimately have no visits.")
    with facts:
        for number, fact in enumerate((report.get("facts") or {}).get("facts", []), 1):
            with st.expander(f"{number}. {fact['what']} · {fact['kind']}"):
                for citation in fact.get("evidence", []):
                    st.write(f"Page {citation['page']} · {citation.get('verification') or 'Not verified'}")
                    st.code(citation["quote"], language=None)
                st.json(fact, expanded=False)
    with findings:
        issues = [{"code": i["issue_code"], "blocking": i["blocking"], "message": i["message"]}
                  for result in report.get("results", []) for i in result.get("issues", [])]
        st.dataframe(issues, width="stretch", hide_index=True) if issues else st.write("No findings returned.")
    with coverage:
        cov = (report.get("facts") or {}).get("accountability", {}).get("coverage", {})
        st.json(cov or report.get("partial_manifests", []), expanded=False)
    with expected:
        if score["checks"]:
            st.dataframe([{**r, "actual": json.dumps(r["actual"]), "expected": json.dumps(r["expected"])}
                          for r in score["checks"]], width="stretch", hide_index=True)
        else:
            st.info("Upload reviewer-authored expectations to measure selected fields on your own protocol.")
    with requests:
        st.json(report["requests"], expanded=False)
        st.caption("Only actual new requests appear here. Cached reader replies can be reused. Schema and baseline caches are separate.")
    with raw:
        st.download_button("Download complete JSON report", json.dumps(report, indent=2),
                           file_name="extraction-report.json", mime="application/json", key="download_report")
        st.json(report, expanded=False)


single, engine_tab, suite, schema_tab = st.tabs(["Extract a document", "Schedule engine", "Case suite", "Response schemas"])
with single:
    source_type = st.radio("Source", ["Built-in synthetic case", "Upload PDF / text"], horizontal=True)
    data = None
    case = None
    expected = None
    filename = "protocol.txt"
    if source_type == "Built-in synthetic case":
        case_id = st.selectbox("Case", list(BY_ID), format_func=lambda key: BY_ID[key].title)
        case = BY_ID[case_id]
        data, filename, expected = case.source.encode(), f"{case.id}.txt", case.expected
        st.text_area("Source document", case.source, height=190, disabled=True)
        with st.expander("Reviewed expectations (never included in live model prompts)"):
            st.json(expected.model_dump(mode="json"), expanded=False)
        st.download_button("Download expectations template", expected.model_dump_json(indent=2),
                           file_name=f"{case.id}-expectations.json", mime="application/json")
    else:
        st.caption("Google's free-tier terms allow content to improve its products. Use synthetic/public documents or documents you have permission to send under those terms.")
        upload = st.file_uploader("Protocol document", type=["pdf", "txt", "md"], max_upload_size=20)
        if upload:
            data, filename = upload.getvalue(), upload.name
        gold_file = st.file_uploader("Optional reviewer-authored expectations", type=["json"])
        if gold_file:
            try:
                if gold_file.size > 256_000:
                    raise ValueError("Expectations file must be under 256 KB.")
                expected = Expectations.model_validate_json(gold_file.getvalue())
            except Exception as error:
                st.error(f"Invalid expectations: {error}")
                data = None
        if not live:
            st.info("Offline replay is available for built-in cases only. Select Gemini API for your own documents.")
    if live and not ready:
        st.caption("Add GEMINI_API_KEY to .env or enter a key in the sidebar to enable live extraction.")
    if st.button("Run extraction", type="primary", disabled=not data or not ready or (not live and case is None)):
        with st.status("Reading source and compiling schedule…", expanded=True) as status:
            budget = RequestBudget(maximum=max_calls, interval_seconds=interval,
                                   progress=lambda message: status.update(label=message))
            report = (extract(data, filename, client=client_for(budget), artifacts=st.session_state.lab_artifacts,
                              limits=limits, expected=expected) if live else
                      replay(case, artifacts=st.session_state.lab_artifacts, limits=limits))
            st.session_state.lab_report = report
            status.update(label=f"Run {report['status'].lower()} · checks {report['score']['status']}",
                          state="error" if report["status"] != "COMPLETED" else "complete", expanded=False)
    if "lab_report" in st.session_state:
        display_report(st.session_state.lab_report)

with engine_tab:
    st.subheader("Check the schedule engine and the schedule tables")
    st.caption("Runs the project's own schedule engine, unchanged, on a compiled schedule. "
               "The tables use the same columns as the app's Protocol and Patient schedule views.")
    pool = {}
    last = st.session_state.get("lab_report")
    if last:
        for number, result in enumerate(last.get("results", []), 1):
            if result.get("schedule"):
                pool[f"Last extraction · {last['filename']} · schedule {number}"] = result["schedule"]
    for key, item in BY_ID.items():
        pool[f"Built-in case · {item.title}"] = key
    pool["Upload a saved report (.json)"] = None
    choice = st.selectbox("Schedule", list(pool))
    schedule = None
    try:
        if pool[choice] is None:
            saved = st.file_uploader("extraction-report.json", type=["json"], key="engine_upload")
            if saved:
                found = [r["schedule"] for r in json.loads(saved.getvalue()).get("results", []) if r.get("schedule")]
                schedule = engine.load_schedule(found[0]) if found else None
                if not found:
                    st.warning("That report contains no compiled schedule.")
        elif isinstance(pool[choice], str):
            schedule = engine.load_schedule(replay(BY_ID[pool[choice]], artifacts=MemoryArtifactStore())["results"][0]["schedule"])
        else:
            schedule = engine.load_schedule(pool[choice])
    except Exception as error:
        st.error(f"Could not load this schedule: {error}")
    if schedule is not None:
        st.markdown("**Protocol schedule** (what the protocol says)")
        rows = engine.protocol_rows(schedule)
        st.dataframe(rows, width="stretch", hide_index=True) if rows else st.info("This schedule has no visits.")
        st.markdown("**Dates for one patient** (what the engine calculates)")
        st.caption("Enter the date each anchor actually happened. An anchor left off stays undated, "
                   "and visits that need it show as waiting. Nothing is guessed.")
        known = {}
        anchors = engine.anchor_codes(schedule)
        columns = st.columns(max(1, min(3, len(anchors))))
        for index, (code, label) in enumerate(anchors):
            with columns[index % len(columns)]:
                if st.checkbox(f"{label} date known", key=f"anchor_on_{code}"):
                    known[code] = st.date_input(label, key=f"anchor_{code}")
        horizon = st.date_input("Calculate dates up to", value=date.today() + timedelta(days=365 * 3), key="engine_horizon")
        try:
            patient, info = engine.patient_rows(schedule, known, horizon=horizon)
            if info.get("blocked_by"):
                st.error("The engine will not date this schedule until these findings are resolved:")
                for line in info["blocked_by"]:
                    st.write("• " + line)
            else:
                st.dataframe(patient, width="stretch", hide_index=True)
                st.caption(f"Engine {info['evaluator']} · {info['events']} visit rows. "
                           "The schedule is treated as approved in memory only; nothing is saved.")
        except Exception as error:
            st.error(f"The engine could not calculate dates: {error}")

with suite:
    st.subheader("Known cases, explicit expectations")
    st.write(f"{len(CASES)} synthetic cases cover timing, missing values, cycles, recurrence, conflicts, scope, table cells and footnotes. A passing case can correctly produce a review blocker.")
    selected = st.multiselect("Cases to run", list(BY_ID), default=[c.id for c in CASES[:3]],
                             format_func=lambda key: BY_ID[key].title)
    st.caption("Live mode needs at least two requests per case, plus any repair calls. The total request cap applies to the whole suite. This finite suite does not prove every possible protocol is correct.")
    if st.button("Run selected cases", disabled=not selected or not ready):
        results = []
        progress = st.progress(0.0)
        label = st.empty()
        budget = RequestBudget(maximum=max_calls, interval_seconds=interval, progress=label.write)
        stopped = None
        for index, case_id in enumerate(selected):
            case = BY_ID[case_id]
            if stopped or (live and budget.calls >= max_calls):
                results.append({"id": case_id, "case": case.title, "status": "NOT_RUN", "reason": stopped or "Request budget reached"})
                continue
            label.write(f"{index + 1}/{len(selected)}: {case.title}")
            report = (extract(case.source.encode(), f"{case.id}.txt", client=client_for(budget),
                              artifacts=st.session_state.lab_artifacts, limits=limits, expected=case.expected)
                      if live else replay(case, artifacts=st.session_state.lab_artifacts, limits=limits))
            results.append({"id": case_id, "case": case.title, "status": report["score"]["status"],
                            "checks": f"{report['score']['passed']}/{report['score']['total']}",
                            "coverage": report["coverage_status"], "report": report})
            failures = " ".join([report.get("error", "")] + [r.get("error", "") for r in report["requests"]])
            if "429" in failures or "RESOURCE_EXHAUSTED" in failures:
                stopped = "Every model in the chain is out of quota"
            progress.progress((index + 1) / len(selected))
        progress.progress(1.0)
        label.write(f"Suite finished. New API requests: {budget.calls}.")
        st.session_state.lab_suite = {"mode": mode, "model": model if live else "No model invoked",
                                     "new_api_requests": budget.calls, "results": results}
    if "lab_suite" in st.session_state:
        bundle = st.session_state.lab_suite
        st.caption(f"Recorded suite: {bundle['mode']} · {bundle['model']} · {bundle['new_api_requests']} new API requests")
        st.dataframe([{k: v for k, v in row.items() if k != "report"} for row in bundle["results"]],
                     width="stretch", hide_index=True)
        st.download_button("Download suite report", json.dumps(bundle, indent=2), file_name="extraction-suite.json", mime="application/json")
        for row in bundle["results"]:
            if "report" in row:
                with st.expander(f"{row['case']} · {row['status']}"):
                    st.json(row["report"]["score"], expanded=False)

with schema_tab:
    stage = st.selectbox("Reader stage", ["metadata", "facts", "repair"])
    st.json(response_schema(stage), expanded=False)
    st.caption("Schema mode sends this schema with application/json to Gemini and validates the returned JSON locally. Baseline mode sends JSON MIME type without a schema. Neither mode verifies clinical meaning by itself.")
    st.markdown("[Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) · [Model pricing / free tier](https://ai.google.dev/gemini-api/docs/pricing) · [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)")
