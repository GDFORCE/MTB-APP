"""Write the constructed protocol fixtures to docs/protocol-benchmarks/synthetic/.

    python -m tests.kit_bench.build            (from backend/)

Per fixture: protocol.pdf (the invented protocol), expected.json (the schedule its rules give,
derived by hand in the fixture module), hand-model.json (a kit model written by hand, as the app
would save it, for checking the engine without any AI). The PDFs are constructed test documents,
marked as such on every page; none is a real protocol.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from app.kit.normalize import build_template_model, normalize  # noqa: E402

from .registry import fixtures  # noqa: E402

OUT = BACKEND.parent / "docs" / "protocol-benchmarks" / "synthetic"


def saved_model(raw: dict) -> dict:
    """The hand-written model as the app saves one (cleaned, review answers applied)."""
    return build_template_model(normalize(raw), {})


def main() -> None:
    index = []
    for fx in fixtures():
        folder = OUT / fx.ID
        folder.mkdir(parents=True, exist_ok=True)
        fx.build_pdf(str(folder / "protocol.pdf"))
        expected = fx.expected()
        (folder / "expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False), encoding="utf-8")
        (folder / "hand-model.json").write_text(
            json.dumps(saved_model(fx.hand_model()), indent=1, ensure_ascii=False), encoding="utf-8")
        index.append((fx.ID, fx.TITLE, fx.COVERS, len(expected["cases"])))
        print(f"{fx.ID}: {len(expected['cases'])} cases")
    lines = ["# Constructed protocol fixtures", "",
             "These PDFs are **invented test documents, not real protocols**. Each is written in a different sponsor "
             "style and carries rules chosen to cover one group of scheduling behaviours. The expected schedules "
             "(`expected.json`) are derived by hand from those rules in `backend/tests/kit_bench/fixtures/`, never "
             "from the app's output. `hand-model.json` is a kit model written by hand for the same rules, used to "
             "check the schedule engine without any AI.", "",
             "Regenerate with `python -m tests.kit_bench.build` (from `backend/`).", "",
             "| Fixture | What it is | Cases | Covers |", "|---|---|---|---|"]
    for fid, title, covers, n in index:
        lines.append(f"| [{fid}]({fid}/) | {title} | {n} | {', '.join(covers)} |")
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
