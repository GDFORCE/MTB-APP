"""Upload a protocol → get the patient's dated visit schedule."""
from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from extract import extract_plan
from schedule import build

HERE = Path(__file__).resolve().parent
for env in (HERE / ".env", HERE.parent / "location_checkin" / ".env", HERE.parents[1] / "backend" / ".env"):
    load_dotenv(env)

st.set_page_config(page_title="Patient Visit Schedule", page_icon="🗓️", layout="wide")
st.markdown("""
<style>
.block-container { padding-top: 2rem; max-width: 1200px; }
.hero { padding: 1.3rem 1.6rem; border-radius: 16px; margin-bottom: 1.2rem; color: #fff;
        background: linear-gradient(120deg,#1e1b4b 0%,#3730a3 55%,#6366f1 100%); }
.hero h1 { font-size: 1.6rem; margin: 0 0 .2rem 0; color: #fff; }
.hero p { margin: 0; opacity: .85; }
</style>
<div class="hero"><h1>🗓️ Patient Visit Schedule</h1>
<p>Upload a protocol → enter the patient's Day 1 → get every visit date.</p></div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Settings")
    api_key = os.environ.get("OPENAI_API_KEY", "") or st.text_input("OpenAI API key", type="password")
    st.caption(f"OpenAI key: {'✅' if api_key else '❌'}")
    model = st.text_input("Model", os.environ.get("OPENAI_SCHEDULE_MODEL", "gpt-4.1"))
    open_repeats = st.number_input("Repeats to show when the protocol gives no end", 1, 100, 6,
                                   help="For visits like 'every cycle until progression'.")

c1, c2 = st.columns([1.4, 1], gap="large")
pdf = c1.file_uploader("Protocol PDF", type=["pdf"])
day1 = c2.date_input("Patient's Day 1 date", date.today(), format="DD/MM/YYYY")
patient = c2.text_input("Patient ID (optional)")

if not pdf:
    st.info("Upload a protocol PDF to start.")
    st.stop()
if not api_key:
    st.warning("Add your OpenAI API key in the sidebar.")
    st.stop()

data = pdf.getvalue()
cache_key = hashlib.sha256(data).hexdigest() + model
if st.session_state.get("plan_key") != cache_key:
    with st.spinner("Reading the protocol and extracting the visit schedule… (about a minute)"):
        try:
            plan, pages = extract_plan(data, api_key, model)
        except Exception as e:
            st.error(f"Extraction failed: {e}")
            st.stop()
    st.session_state.update(plan_key=cache_key, plan=plan, pages=pages)

plan, pages = st.session_state.plan, st.session_state.pages
rows = build(plan, day1, int(open_repeats))
if not rows:
    st.error("No visits were found in this protocol.")
    st.stop()

st.subheader(plan.get("study_title") or pdf.name)
if plan.get("day1_definition"):
    st.caption(f"Day 1 in this protocol = **{plan['day1_definition']}**")

dated = [r for r in rows if r.target]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Visits", len(rows))
m2.metric("First visit", dated[0].target.strftime("%d %b %Y") if dated else "—")
m3.metric("Last dated visit", dated[-1].target.strftime("%d %b %Y") if dated else "—")
m4.metric("Weekend visits", sum(r.target.weekday() >= 5 for r in dated))


def fmt(d):
    return d.strftime("%d %b %Y") if d else ""


table = pd.DataFrame([{
    "Visit": r.visit,
    "Study day": r.study_day if r.study_day is not None else "—",
    "Date": fmt(r.target) or "Event-driven",
    "Day of week": r.target.strftime("%a") if r.target else "",
    "Window": (f"{fmt(r.earliest)} → {fmt(r.latest)}" if r.target and r.earliest != r.latest else ""),
    "Activities": ", ".join(r.activities),
    "Note": r.note,
} for r in rows])


def weekend(row):
    return ["background-color: rgba(245,158,11,.15)" if row["Day of week"] in ("Sat", "Sun") else ""] * len(row)


st.dataframe(table.style.apply(weekend, axis=1), hide_index=True, use_container_width=True,
             height=min(38 * (len(table) + 1) + 4, 720))
st.caption("Highlighted rows fall on a weekend. Move them within the visit window if the site is closed.")

fname = f"visit_schedule_{patient or 'patient'}_{day1:%Y%m%d}.csv"
st.download_button("⬇ Download schedule (CSV)", table.to_csv(index=False).encode(), fname, "text/csv", type="primary")

with st.expander("What the AI extracted"):
    st.caption(f"Read from pages {', '.join(map(str, pages))}.")
    if plan.get("notes"):
        st.write(plan["notes"])
    st.json(plan, expanded=False)
