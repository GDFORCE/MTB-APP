"""Streamlit prototype: photo → machine identification → two-tier location check-in."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import folium
from folium.plugins import Draw
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium
from streamlit_js_eval import streamlit_js_eval

import services as svc
from checkin import Fix, Outcome, Thresholds, contains, evaluate

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parents[1] / "backend" / ".env")

st.set_page_config(page_title="Location-Verified Check-In", page_icon="📍", layout="wide")

st.markdown("""
<style>
:root { --ink:inherit; --muted:rgba(128,128,128,.95); --line:rgba(128,128,128,.28); --teal:#0d9488;
        --card:rgba(128,128,128,.07); }
.block-container { padding-top: 2rem; max-width: 1280px; }
.hero { padding: 1.4rem 1.6rem; border-radius: 16px;
        background: linear-gradient(120deg,#0f172a 0%,#134e4a 60%,#0d9488 100%); color:#f8fafc; margin-bottom:1.4rem; }
.hero h1 { font-size: 1.7rem; margin: 0 0 .25rem 0; color:#fff; }
.hero p { margin: 0; opacity:.85; }
.step { display:flex; align-items:center; gap:.6rem; margin: 1.2rem 0 .6rem; }
.step .n { width:28px; height:28px; border-radius:50%; background:var(--teal); color:#fff;
           display:grid; place-items:center; font-weight:700; font-size:.85rem; }
.step .t { font-weight:700; font-size:1.1rem; }
.card { border:1px solid var(--line); border-radius:14px; padding:1rem 1.2rem; background:var(--card); color:var(--ink); }
.kv { display:grid; grid-template-columns: 150px 1fr; row-gap:.35rem; font-size:.93rem; }
.kv .k { color:var(--muted); }
.chip { display:inline-block; padding:.15rem .55rem; margin:.15rem .2rem 0 0; border-radius:999px;
        background:var(--card); border:1px solid var(--line); font-size:.8rem; color:inherit; }
.badge { display:inline-block; padding:.12rem .5rem; border-radius:6px; font-size:.75rem; font-weight:700; }
.verdict { border-radius:14px; padding:1.1rem 1.3rem; margin-bottom:.8rem; color:#fff; }
.verdict h3 { margin:0 0 .2rem 0; color:#fff; }
.verdict p { margin:0; opacity:.92; }
</style>
""", unsafe_allow_html=True)

VERDICTS = {
    Outcome.VERIFIED: ("#059669", "Verified check-in", "User is on campus and at the department."),
    Outcome.VERIFIED_LENIENT: ("#0d9488", "Verified (lenient, weak signal)",
                               "On campus. Department match needed the expanded radius for a degraded indoor signal."),
    Outcome.CAMPUS_ONLY: ("#2563eb", "On campus", "Campus confirmed. No department target was selected."),
    Outcome.PARTIAL_MANUAL_BACKUP: ("#d97706", "Partial access: manual backup needed",
                                    "On campus, but not confirmed at the department."),
    Outcome.QR_FALLBACK: ("#7c3aed", "Weak signal: scan the QR code",
                          "We see your signal is weak inside the building. Please scan the check-in QR code "
                          "at the department reception desk to complete your confirmation."),
    Outcome.DENIED_OUT_OF_BOUNDS: ("#dc2626", "Access denied: out of bounds", "Location is outside the campus boundary."),
    Outcome.REJECTED_STALE: ("#475569", "Location too old", "Request a fresh location fix."),
}

ss = st.session_state
for k, v in {"ident": None, "img": None, "hospitals": [], "hospital": None, "campus": None, "campus_src": None,
             "targets": [], "target": None, "fix": None, "fix_src": None, "geo_nonce": 0, "want_geo": False,
             "last_click": None, "last_drawing": None, "tile_sessions": {}}.items():
    ss.setdefault(k, v)


def step(n: int, title: str) -> None:
    st.markdown(f'<div class="step"><div class="n">{n}</div><div class="t">{title}</div></div>', unsafe_allow_html=True)


def name(place: dict) -> str:
    return place.get("displayName", {}).get("text", place.get("id", "?"))


def loc(place: dict):
    p = place.get("location")
    return (p["latitude"], p["longitude"]) if p else None


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Configuration")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        openai_key = st.text_input("OpenAI API key", type="password")
    places_key = os.environ.get("GOOGLE_PLACES_API_KEY", "") or st.text_input("Google Places API key", type="password")
    st.caption(f"OpenAI key: {'✅' if openai_key else '❌'} · Google key: {'✅' if places_key else '❌'}")
    vision_model = st.text_input("Vision model", os.environ.get("OPENAI_VISION_MODEL", "gpt-4o"))

    st.subheader("Thresholds")
    st.caption("PRD values, except the unresolvable limit, which the PRD doesn't specify.")
    th = Thresholds(
        max_age_s=st.number_input("Max fix age (s)", 1.0, 600.0, 45.0),
        standard_radius_m=st.number_input("Standard department radius (m)", 1.0, 500.0, 20.0),
        degraded_accuracy_m=st.number_input("Degraded when accuracy > (m)", 1.0, 500.0, 30.0),
        degraded_radius_cap_m=st.number_input("Expanded radius cap (m)", 1.0, 1000.0, 50.0),
        unresolvable_accuracy_m=st.number_input("⚠ Unresolvable when accuracy > (m)", 1.0, 5000.0, 100.0),
    )

st.markdown("""<div class="hero"><h1>📍 Location-Verified Equipment Check-In</h1>
<p>Photo → AI machine identification → campus polygon (Tier 1) → department proximity (Tier 2)</p></div>""",
            unsafe_allow_html=True)

# ---------------------------------------------------------------- 1. photo

step(1, "Capture the machine")
c1, c2 = st.columns([1, 1.25], gap="large")
with c1:
    tab_up, tab_cam = st.tabs(["Upload photo", "Use camera"])
    with tab_up:
        up = st.file_uploader("Photo", type=["jpg", "jpeg", "png", "webp"], label_visibility="collapsed")
    with tab_cam:
        cam = st.camera_input("Camera", label_visibility="collapsed")
    photo = up or cam
    if photo:
        data = photo.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if not ss.img or ss.img["digest"] != digest:
            ss.img = {"bytes": data, "mime": photo.type or "image/jpeg", "digest": digest,
                      "exif": svc.exif_location(data)}
            ss.ident = None
        st.image(data, use_container_width=True)

# ---------------------------------------------------------------- 2. identification

with c2:
    step(2, "AI identification")
    if not ss.img:
        st.info("Upload or capture a photo to begin.")
    else:
        if st.button("🔍 Identify machine", type="primary", disabled=not openai_key):
            with st.spinner(f"Analysing with {vision_model}…"):
                try:
                    ss.ident = svc.identify_machine(ss.img["bytes"], ss.img["mime"], openai_key, vision_model)
                except Exception as e:  # surfaced to the operator, not swallowed
                    st.error(f"Identification failed: {e}")
        if not openai_key:
            st.warning("Add an OpenAI API key in the sidebar.")
        idn = ss.ident
        if idn:
            conf = float(idn.get("confidence") or 0)
            none = "<i style='color:#94a3b8'>not visible</i>"
            chips = "".join(f'<span class="chip">{t}</span>' for t in idn.get("visible_text", [])) or none
            clues = "".join(f'<span class="chip">{t}</span>' for t in idn.get("setting_clues", [])) or none
            badge = ("#dcfce7;color:#166534", "Medical equipment") if idn.get("is_medical_equipment") \
                else ("#fee2e2;color:#991b1b", "Not medical equipment")
            st.markdown(f"""<div class="card">
<div style="display:flex;justify-content:space-between;align-items:center">
  <div style="font-size:1.3rem;font-weight:800">{idn.get('machine_type')}</div>
  <span class="badge" style="background:{badge[0]}">{badge[1]}</span></div>
<div class="kv" style="margin-top:.7rem">
  <div class="k">Manufacturer</div><div>{idn.get('manufacturer') or none}</div>
  <div class="k">Model</div><div>{idn.get('model') or none}</div>
  <div class="k">Serial / asset tag</div><div>{idn.get('serial_or_asset_tag') or none}</div>
  <div class="k">Likely department</div><div><b>{idn.get('likely_department') or none}</b></div>
  <div class="k">Visible text</div><div>{chips}</div>
  <div class="k">Location clues</div><div>{clues}</div>
</div></div>""", unsafe_allow_html=True)
            st.progress(conf, text=f"Model confidence {conf:.0%}")
            if idn.get("notes"):
                st.caption(idn["notes"])

# ---------------------------------------------------------------- 3. hospital & department

step(3, "Hospital campus & department target")
if not places_key:
    st.warning("Add a Google Places API key to search hospitals.")
h1, h2 = st.columns([1, 1], gap="large")
with h1:
    with st.form("hsearch", border=False):
        q = st.text_input("Search hospital", placeholder="e.g. Apollo Hospitals Jubilee Hills")
        go = st.form_submit_button("Search", disabled=not places_key)
    if go and q:
        try:
            near = (ss.fix["lat"], ss.fix["lng"]) if ss.fix else None
            ss.hospitals = svc.search_hospitals(q, places_key, near)
        except Exception as e:
            st.error(str(e))
    if ss.hospitals:
        labels = [f"{name(p)} · {p.get('formattedAddress', '')}" for p in ss.hospitals]
        i = st.selectbox("Select hospital", range(len(labels)), format_func=lambda j: labels[j])
        use_osm = st.checkbox("Auto-fill outline from OpenStreetMap", value=False,
                              help="Google has no campus-boundary API. Leave off to trace the campus on Google "
                                   "satellite yourself.")
        if st.button("Use this hospital"):
            with st.spinner("Loading campus boundary and sub-places…"):
                try:
                    hosp = svc.place_details(ss.hospitals[i]["id"], places_key)
                    ss.hospital, ss.target = hosp, None
                    poly, ss.osm_error, ss.osm_tried = None, None, use_osm
                    if use_osm:
                        try:
                            poly = svc.osm_campus_polygon(loc(hosp))
                        except Exception as e:
                            ss.osm_error = str(e)
                    if poly:
                        ss.campus, ss.campus_src = poly, "OpenStreetMap hospital outline"
                    else:
                        ss.campus, ss.campus_src = svc.viewport_polygon(hosp), "Google viewport rectangle (approximate)"
                    subs = svc.sub_destinations(hosp, places_key)
                    ss.targets = [dict(p, _src="Google sub-destination") for p in subs if loc(p)]
                    ss.target_sel = None
                except Exception as e:
                    st.error(str(e))

with h2:
    if ss.hospital:
        hosp = ss.hospital
        ok = not ss.campus_src.startswith("Google viewport")
        st.markdown(f"""<div class="card"><div style="font-weight:800;font-size:1.05rem">{name(hosp)}</div>
<div style="color:var(--muted);font-size:.85rem">{hosp.get('formattedAddress', '')}</div>
<div style="margin-top:.5rem"><span class="badge" style="background:{'#dcfce7;color:#166534' if ok else '#fef3c7;color:#92400e'}">
Campus: {ss.campus_src}</span> <span class="chip">{len(ss.campus)} vertices</span>
<span class="chip">{len(hosp.get('subDestinations', []))} Google sub-places</span></div></div>""",
                    unsafe_allow_html=True)
        if not ok:
            if ss.get("osm_error"):
                why = "The OpenStreetMap servers didn't respond."
            elif ss.get("osm_tried"):
                why = "This hospital has no outline in OpenStreetMap."
            else:
                why = "Google doesn't publish campus boundaries."
            st.warning(f"{why} Using Google's display rectangle for now. It isn't the property line, so Tier 1 "
                       "is approximate. For an exact boundary, go to step 4, set **Map click sets** to "
                       "**Draw campus outline**, and trace the grounds on the Google satellite map in step 5.")
            if ss.get("osm_error"):
                st.caption(ss.osm_error)
        dept_default = (ss.ident or {}).get("likely_department") or ""
        dcol1, dcol2 = st.columns([3, 1])
        dept = dcol1.text_input("Department to locate", dept_default, placeholder="e.g. Radiology")
        if dcol2.button("Find", use_container_width=True) and dept:
            try:
                found = [p for p in svc.search_department(dept, hosp, places_key) if loc(p)]
                on_campus = [p for p in found if contains(ss.campus, loc(p))]
                known = {t["id"] for t in ss.targets}
                ss.targets += [dict(p, _src=f"Search: {dept}") for p in on_campus if p["id"] not in known]
                if len(found) > len(on_campus):
                    st.caption(f"Dropped {len(found) - len(on_campus)} result(s) located outside the campus polygon.")
                if not on_campus:
                    st.info("No on-campus Google place found for that department. Click the map to pin it.")
            except Exception as e:
                st.error(str(e))
        if ss.targets:
            opts = ["— none (campus only) —"] + [f"{name(t)} · {t['_src']}" for t in ss.targets]
            sel = st.selectbox("Department target (Tier 2)", range(len(opts)), format_func=lambda j: opts[j])
            if sel != ss.get("target_sel"):
                ss.target_sel = sel
                if sel:
                    t = ss.targets[sel - 1]
                    ss.target = {"lat": loc(t)[0], "lng": loc(t)[1], "label": name(t), "src": t["_src"]}
                else:
                    ss.target = None
        if ss.target:
            st.success(f"Target: **{ss.target['label']}** ({ss.target['src']})")

# ---------------------------------------------------------------- 4. location fix

step(4, "Device location fix")
src = st.radio("Location source", ["This device (live GPS/Wi-Fi)", "Photo EXIF GPS", "Simulate (click map)"],
               horizontal=True)

if src.startswith("This device"):
    if st.button("📡 Get my location"):
        ss.want_geo, ss.geo_nonce = True, ss.geo_nonce + 1
    if ss.want_geo:
        js = ("new Promise(r => navigator.geolocation.getCurrentPosition("
              "p => r({lat:p.coords.latitude,lng:p.coords.longitude,accuracy:p.coords.accuracy,ts:p.timestamp}),"
              "e => r({error:e.code,message:e.message}),"
              "{enableHighAccuracy:true,maximumAge:0,timeout:20000}))")
        res = streamlit_js_eval(js_expressions=js, key=f"geo{ss.geo_nonce}")
        if res is None:
            st.info("Waiting for the browser. Allow location access if prompted.")
        elif "error" in res:
            ss.want_geo = False
            ss.fix = None
            st.error(f"Location error {res['error']}: {res.get('message')}")
            st.markdown("**Fallback:** scan the check-in QR code at the department reception desk.")
        else:
            ss.want_geo = False
            ss.fix = {"lat": res["lat"], "lng": res["lng"], "accuracy_m": res["accuracy"], "ts": res["ts"] / 1000}
            ss.fix_src = "live"
elif src.startswith("Photo EXIF"):
    ex = ss.img and ss.img["exif"]
    if not ex:
        st.warning("The photo has no EXIF GPS data. Messaging apps and screenshots usually strip it.")
    else:
        st.caption(f"Taken {ex['taken'] or 'at unknown time'}. EXIF accuracy: "
                   f"{'±%.0fm' % ex['accuracy_m'] if ex['accuracy_m'] is not None else 'not recorded'}. "
                   "A photo's location is historical, so freshness usually fails. That's intended.")
        if st.button("Use photo location"):
            ss.fix = {"lat": ex["lat"], "lng": ex["lng"], "accuracy_m": ex["accuracy_m"],
                      "age_s": ex["age_s"]}
            ss.fix_src = "exif"
else:
    s1, s2 = st.columns(2)
    sim_acc = s1.slider("Simulated accuracy (m)", 1, 300, 12)
    sim_age = s2.slider("Simulated fix age (s)", 0, 300, 5)
    st.caption("Click the map below to place the simulated device.")
    if ss.fix and ss.fix_src == "sim":
        ss.fix.update(accuracy_m=float(sim_acc), age_s=float(sim_age))

modes = (["Simulated device position"] if src.startswith("Simulate") else []) + \
    ["Department target pin", "Draw campus outline"]
click_mode = st.radio("Map click sets", modes, horizontal=True)
if click_mode == "Draw campus outline":
    st.caption("Use the polygon tool (⬟, top-left of the map) to click around the hospital grounds on satellite, "
               "then click the first point to close it. The drawn shape replaces the current campus boundary.")

# ---------------------------------------------------------------- 5. result + map

step(5, "Verification")
fix_obj = None
if ss.fix:
    age = time.time() - ss.fix["ts"] if ss.fix_src == "live" else ss.fix.get("age_s")
    fix_obj = Fix(ss.fix["lat"], ss.fix["lng"], ss.fix["accuracy_m"], age)

r1, r2 = st.columns([1, 1.6], gap="large")
with r1:
    if not ss.campus:
        st.info("Select a hospital to load its campus boundary.")
    elif not fix_obj:
        st.info("Get a location fix to run the check.")
    else:
        target = (ss.target["lat"], ss.target["lng"]) if ss.target else None
        res = evaluate(fix_obj, ss.campus, target, th)
        color, title, blurb = VERDICTS[res.outcome]
        st.markdown(f'<div class="verdict" style="background:{color}"><h3>{title}</h3><p>{blurb}</p></div>',
                    unsafe_allow_html=True)
        m1, m2, m3 = st.columns(3)
        m1.metric("Tier 1 campus", {True: "Inside", False: "Outside", None: "—"}[res.inside_campus])
        m2.metric("To department", f"{res.distance_m:.1f} m" if res.distance_m is not None else "—")
        m3.metric("Radius used", f"{res.radius_used_m:.0f} m" if res.radius_used_m else "—")
        st.markdown("**Decision trace**")
        for line in res.reasons:
            st.markdown(f"- {line}")
        if ss.fix_src == "live" and fix_obj.age_s is not None:
            st.caption(f"Fix age is recomputed on every rerun ({fix_obj.age_s:.0f}s now), "
                       "so a fix eventually goes stale if you wait.")
        if ss.ident:
            st.markdown("**Check-in record**")
            st.json({"machine": {k: ss.ident.get(k) for k in
                                 ("machine_type", "manufacturer", "model", "serial_or_asset_tag")},
                     "hospital_place_id": ss.hospital["id"] if ss.hospital else None,
                     "department": ss.target, "fix": ss.fix, "fix_source": ss.fix_src,
                     "outcome": res.outcome.value}, expanded=False)

with r2:
    center = (loc(ss.hospital) if ss.hospital else None) or \
             ((ss.fix["lat"], ss.fix["lng"]) if ss.fix else (17.385, 78.4867))
    fmap = folium.Map(location=center, zoom_start=17 if ss.hospital else 12, tiles=None, control_scale=True,
                      max_zoom=21)
    drawing = click_mode == "Draw campus outline"
    layers = [("Google Hybrid", "satellite", True), ("Google Roadmap", "roadmap", False),
              ("Google Satellite", "satellite", False)]
    if not drawing:
        layers = [layers[1], layers[0], layers[2]]
    try:
        for label, map_type, roads in layers:
            if label not in ss.tile_sessions:
                ss.tile_sessions[label] = svc.google_tile_session(places_key, map_type, roads)
            folium.TileLayer(svc.google_tile_url(places_key, ss.tile_sessions[label]), name=label,
                             attr="Map data ©Google", max_zoom=21, max_native_zoom=20).add_to(fmap)
    except Exception as e:
        st.error(f"Google Map Tiles unavailable: {e}")
    if drawing:
        Draw(draw_options={"polyline": False, "rectangle": False, "circle": False, "marker": False,
                           "circlemarker": False, "polygon": {"allowIntersection": False}},
             edit_options={"edit": False, "remove": False}).add_to(fmap)
    if ss.campus:
        folium.Polygon(ss.campus, color="#0d9488", weight=3, fill=True, fill_opacity=.12,
                       tooltip=f"Campus · {ss.campus_src}").add_to(fmap)
    for t in ss.targets:
        if loc(t):
            folium.CircleMarker(loc(t), radius=4, color="#64748b", fill=True, tooltip=name(t)).add_to(fmap)
    if ss.target:
        tl = (ss.target["lat"], ss.target["lng"])
        folium.Marker(tl, tooltip=ss.target["label"], icon=folium.Icon(color="red", icon="plus-sign")).add_to(fmap)
        folium.Circle(tl, radius=th.standard_radius_m, color="#059669", weight=2, fill=False,
                      tooltip=f"Standard {th.standard_radius_m:.0f} m").add_to(fmap)
        folium.Circle(tl, radius=th.degraded_radius_cap_m, color="#d97706", weight=1, dash_array="6",
                      fill=False, tooltip=f"Expanded cap {th.degraded_radius_cap_m:.0f} m").add_to(fmap)
    if ss.fix:
        fl = (ss.fix["lat"], ss.fix["lng"])
        folium.Marker(fl, tooltip="Device", icon=folium.Icon(color="blue", icon="user")).add_to(fmap)
        if ss.fix.get("accuracy_m"):
            folium.Circle(fl, radius=ss.fix["accuracy_m"], color="#2563eb", weight=1, fill=True,
                          fill_opacity=.1, tooltip=f"Accuracy ±{ss.fix['accuracy_m']:.0f} m").add_to(fmap)
    folium.LayerControl(collapsed=True).add_to(fmap)
    out = st_folium(fmap, height=520, use_container_width=True,
                    returned_objects=["last_clicked", "last_active_drawing"], key="map") or {}

    drawn = out.get("last_active_drawing")
    if drawing and drawn and drawn != ss.last_drawing:
        ss.last_drawing = drawn
        ring = drawn.get("geometry", {}).get("coordinates", [[]])[0]
        if len(ring) >= 4:
            ss.campus = [(lat, lng) for lng, lat in ring[:-1]]
            ss.campus_src = "Drawn on Google satellite"
            st.rerun()

    clicked = out.get("last_clicked")
    if not drawing and clicked and clicked != ss.last_click:
        ss.last_click = clicked
        if click_mode.startswith("Simulated"):
            ss.fix = {"lat": clicked["lat"], "lng": clicked["lng"], "accuracy_m": float(sim_acc),
                      "age_s": float(sim_age)}
            ss.fix_src = "sim"
        else:
            ss.target = {"lat": clicked["lat"], "lng": clicked["lng"], "label": "Pinned department",
                         "src": "Pinned on map"}
        st.rerun()
