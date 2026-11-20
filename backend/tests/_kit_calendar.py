"""Compute calendars from server payloads with the app's own TypeScript engine and patient consumer.

``calendars({"name": payload})`` runs frontend/scripts/kit-schedule-cli.ts once per payload (each
payload carries its own workflow version's model) and returns, per name::

    {"staff": {visit key: row}, "patient": {visit key: row}, "schedule": <full serialized schedule>}

"staff" is computeKitSchedule (the staff calendar, KitPatientView); "patient" is kitVisitRows (the
rows the patient dashboard, calendar and visit screens read). No network, no database.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def calendars(payloads: dict[str, dict]) -> dict[str, dict]:
    out = {}
    with tempfile.TemporaryDirectory() as folder:
        for name, payload in payloads.items():
            source, target = Path(folder) / f"{name}.in.json", Path(folder) / f"{name}.out.json"
            source.write_text(json.dumps({
                "model": payload["model"], "site": payload.get("site_answers") or {},
                "records": {name: payload["participant"]}, "payloads": {name: payload},
            }), encoding="utf-8")
            done = subprocess.run(
                ["node", "--experimental-strip-types", "--import", "./tests/kit-resolve-hook.mjs",
                 "scripts/kit-schedule-cli.ts", str(source), str(target)],
                cwd=FRONTEND, capture_output=True, timeout=60)
            assert done.returncode == 0, done.stderr.decode(errors="replace")[-2000:]
            result = json.loads(target.read_text(encoding="utf-8"))
            schedule, patient = result[name], result[f"patient:{name}"]
            assert "error" not in schedule, schedule
            assert isinstance(patient, list), patient
            out[name] = {"schedule": schedule, "staff": {r["key"]: r for r in schedule["rows"]},
                         "patient": {r["id"]: r for r in patient}}
    return out


def calendar(payload: dict) -> dict:
    return calendars({"one": payload})["one"]
