"""External calls: OpenAI vision, Google Places (New), OSM Overpass, photo EXIF."""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image

from checkin import contains

LatLng = Tuple[float, float]

PLACES_BASE = "https://places.googleapis.com/v1"
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# ---------------------------------------------------------------- OpenAI vision

MACHINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "is_medical_equipment": {"type": "boolean"},
        "machine_type": {"type": "string", "description": "Generic category, e.g. 'MRI scanner', 'Infusion pump'."},
        "manufacturer": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "serial_or_asset_tag": {"type": ["string", "null"]},
        "visible_text": {"type": "array", "items": {"type": "string"}},
        "likely_department": {"type": ["string", "null"],
                              "description": "Hospital department where this machine usually lives, e.g. 'Radiology'."},
        "setting_clues": {"type": "array", "items": {"type": "string"},
                          "description": "Visible clues about the room/location (signage, room numbers)."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
    },
    "required": ["is_medical_equipment", "machine_type", "manufacturer", "model", "serial_or_asset_tag",
                 "visible_text", "likely_department", "setting_clues", "confidence", "notes"],
}

VISION_PROMPT = (
    "You are identifying hospital or clinical equipment from a photo. "
    "Report only what is visible or strongly implied. Use null for manufacturer, model, serial "
    "or department when not legible or not inferable; never guess a serial number. "
    "Transcribe any legible labels, nameplates, or signage into visible_text."
)


def identify_machine(image_bytes: bytes, mime: str, api_key: str, model: str) -> Dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": VISION_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Identify the machine in this photo."},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ]},
        ],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "machine_identification", "strict": True, "schema": MACHINE_SCHEMA}},
    )
    msg = resp.choices[0].message
    if msg.refusal:
        raise RuntimeError(f"Model refused: {msg.refusal}")
    return json.loads(msg.content or "{}")


# ---------------------------------------------------------------- Google Places (New)

def _places(method: str, path: str, key: str, field_mask: str, body: Optional[dict] = None) -> dict:
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": field_mask, "Content-Type": "application/json"}
    r = requests.request(method, f"{PLACES_BASE}{path}", headers=headers, json=body, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"Places API {r.status_code}: {r.text[:300]}")
    return r.json()


def search_hospitals(query: str, key: str, near: Optional[LatLng] = None) -> List[dict]:
    body: Dict[str, Any] = {"textQuery": query, "pageSize": 8}
    if near:
        body["locationBias"] = {"circle": {"center": {"latitude": near[0], "longitude": near[1]}, "radius": 20000}}
    data = _places("POST", "/places:searchText", key,
                   "places.id,places.displayName,places.formattedAddress,places.location,places.viewport,places.types",
                   body)
    return data.get("places", [])


def place_details(place_id: str, key: str) -> dict:
    return _places("GET", f"/places/{place_id}", key,
                   "id,displayName,formattedAddress,location,viewport,types,subDestinations")


def sub_destinations(hospital: dict, key: str) -> List[dict]:
    """Google-registered sub-places (wings, clinics) of the parent place."""
    out = []
    for sd in hospital.get("subDestinations", [])[:15]:
        try:
            out.append(_places("GET", f"/places/{sd['id']}", key, "id,displayName,location,types"))
        except RuntimeError:
            continue
    return out


def search_department(dept: str, hospital: dict, key: str) -> List[dict]:
    """Text-search a department restricted to the hospital's viewport."""
    vp = hospital.get("viewport")
    body: Dict[str, Any] = {"textQuery": f"{dept} {hospital['displayName']['text']}", "pageSize": 5}
    if vp:
        body["locationRestriction"] = {"rectangle": {"low": vp["low"], "high": vp["high"]}}
    data = _places("POST", "/places:searchText", key, "places.id,places.displayName,places.location,places.types", body)
    return [p for p in data.get("places", []) if p["id"] != hospital["id"]]


def viewport_polygon(place: dict) -> Optional[List[LatLng]]:
    vp = place.get("viewport")
    if not vp:
        return None
    lo, hi = vp["low"], vp["high"]
    return [(lo["latitude"], lo["longitude"]), (lo["latitude"], hi["longitude"]),
            (hi["latitude"], hi["longitude"]), (hi["latitude"], lo["longitude"])]


# ---------------------------------------------------------------- Google Map Tiles

def google_tile_session(key: str, map_type: str, overlay_roads: bool = False) -> str:
    body: Dict[str, Any] = {"mapType": map_type, "language": "en-US", "region": "IN"}
    if overlay_roads:
        body["layerTypes"] = ["layerRoadmap"]
    r = requests.post(f"https://tile.googleapis.com/v1/createSession?key={key}", json=body, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"Map Tiles API {r.status_code}: {r.text[:200]}")
    return r.json()["session"]


def google_tile_url(key: str, session: str) -> str:
    return f"https://tile.googleapis.com/v1/2dtiles/{{z}}/{{x}}/{{y}}?session={session}&key={key}"


# ---------------------------------------------------------------- OSM campus boundary

def osm_campus_polygon(center: LatLng, radius_m: int = 400) -> Optional[List[LatLng]]:
    """Closest-containing hospital outline mapped as a way in OSM, or None."""
    lat, lng = center
    q = (f'[out:json][timeout:20];(way["amenity"="hospital"](around:{radius_m},{lat},{lng});'
         f'way["healthcare"="hospital"](around:{radius_m},{lat},{lng}););out geom;')
    errors = []
    for url in OVERPASS_MIRRORS:
        try:
            r = requests.post(url, data={"data": q}, timeout=25,
                              headers={"User-Agent": "MTB-LocationCheckin-Prototype/0.1"})
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{url.split('/')[2]}: {e.__class__.__name__}")
    else:
        raise RuntimeError("all OpenStreetMap servers failed (" + "; ".join(errors) + ")")
    rings = []
    for el in elements:
        geom = el.get("geometry") or []
        if len(geom) >= 4:
            rings.append([(n["lat"], n["lon"]) for n in geom])
    if not rings:
        return None
    containing = [ring for ring in rings if contains(ring, center)]
    candidates = containing or rings
    return max(candidates, key=_ring_area)


def _ring_area(ring: List[LatLng]) -> float:
    return abs(sum(a[1] * b[0] - b[1] * a[0] for a, b in zip(ring, ring[1:] + ring[:1]))) / 2


# ---------------------------------------------------------------- Photo EXIF

def exif_location(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """GPS lat/lng, horizontal error and capture age from EXIF, when present."""
    img = Image.open(io.BytesIO(image_bytes))
    exif = img.getexif()
    gps = exif.get_ifd(0x8825)
    if not gps or 2 not in gps or 4 not in gps:
        return None

    def to_deg(v, ref):
        d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
        return -d if ref in ("S", "W") else d

    lat = to_deg(gps[2], gps.get(1, "N"))
    lng = to_deg(gps[4], gps.get(3, "E"))
    accuracy = float(gps[31]) if 31 in gps else None

    age_s = None
    sub = exif.get_ifd(0x8769)
    taken, offset = sub.get(36867), sub.get(36881)
    if taken and offset:
        try:
            sign = 1 if offset[0] == "+" else -1
            hh, mm = offset[1:].split(":")
            tz = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
            dt = datetime.strptime(taken, "%Y:%m:%d %H:%M:%S").replace(tzinfo=tz)
            age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        except ValueError:
            pass
    return {"lat": lat, "lng": lng, "accuracy_m": accuracy, "age_s": age_s, "taken": taken}
