"""Write the normalized form of each synthetic kit fixture next to its raw model.

    python scripts/kit_synthetic_fixtures.py          # rewrite tests/fixtures/kit_synthetic/*.model.json
    python scripts/kit_synthetic_fixtures.py --check  # exit 1 when a written file is out of date

The frontend engine tests read the ``*.model.json`` files, so the TypeScript scenarios run on exactly
what the backend's clean-up produces (backend/tests/test_kit_adaptive_study.py checks they are fresh).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.kit.normalize import normalize  # noqa: E402

FOLDER = BACKEND / "tests" / "fixtures" / "kit_synthetic"


def normalized(raw_path: Path) -> str:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw.pop("_fixture", None)
    return json.dumps(normalize(copy.deepcopy(raw)), indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    stale = []
    for raw_path in sorted(FOLDER.glob("*.raw.json")):
        target = raw_path.with_name(raw_path.name.replace(".raw.json", ".model.json"))
        text = normalized(raw_path)
        if "--check" in sys.argv:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(target.name)
        else:
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target.relative_to(BACKEND)}")
    if stale:
        print("out of date: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
