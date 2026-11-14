"""Run the kit analysis on one protocol file and keep everything each step saw and produced.

    python scripts/kit_bench.py <protocol.pdf|.txt> <output folder> [--replay <earlier output folder>]

Written into the output folder:
  prepared.json            what reading the PDF gave (table pages, hidden pages, sizes; no images)
  page_texts.json          the text of every page as the model receives it
  calls/NN-<stage>.prompt.txt / .reply.json
                           every model request and the parsed reply, in order
  normalize/NN.in.json / .out.json
                           the model before and after each clean-up pass
  result.json              the analysis result the app would show (baseModel, model, warnings...)
  stages.log               the stage messages

--replay answers every model request from the replies recorded in an earlier folder (same order),
so clean-up and merge changes can be measured again without spending model credits. A replay is
only faithful while the prompts are unchanged; the folder notes which prompts differ.

Nothing is written to the database. The protocol file is sent to the model provider configured in
backend/.env (KIT_EXTRACTION_MODEL), exactly as an upload in the app would.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.kit import analyze as kit_analyze  # noqa: E402
from app.kit.llm import AnalysisError, OpenAIKitClient  # noqa: E402
from app.kit.pdf_input import prepare_protocol_input  # noqa: E402


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


class Recorder:
    """Wraps the model client: saves each prompt and reply, or replays saved replies."""

    def __init__(self, out: Path, client: Any | None, replay: Path | None):
        self.out, self.client, self.replay = out, client, replay
        self.n = 0
        self.stage = "tables"
        self.model = getattr(client, "model", "replay")
        self.on_status: Any = None
        self.prompt_changes: list[str] = []

    def json(self, prompt: str, *, images: list[dict[str, Any]] | None = None, on_text: Any = None) -> Any:
        self.n += 1
        name = f"{self.n:02d}-{self.stage}"
        (self.out / "calls").mkdir(parents=True, exist_ok=True)
        (self.out / "calls" / f"{name}.prompt.txt").write_text(prompt, encoding="utf-8")
        started = time.time()
        if self.replay is not None:
            found = sorted((self.replay / "calls").glob(f"{self.n:02d}-*.reply.json"))
            if not found:
                raise AnalysisError("upstream_error", f"replay: no recorded reply for request {self.n}")
            old_prompt = found[0].with_name(found[0].name.replace(".reply.json", ".prompt.txt"))
            if old_prompt.is_file() and old_prompt.read_text(encoding="utf-8") != prompt:
                self.prompt_changes.append(found[0].name)
            reply = json.loads(found[0].read_text(encoding="utf-8"))
            if isinstance(reply, dict) and reply.get("__error__"):
                raise AnalysisError(reply["__error__"], reply.get("message") or "recorded error")
        else:
            assert self.client is not None
            self.client.on_status = self.on_status
            try:
                reply = self.client.json(prompt, images=images, on_text=on_text)
            except AnalysisError as error:
                _dump(self.out / "calls" / f"{name}.reply.json", {"__error__": error.code, "message": str(error)})
                raise
        _dump(self.out / "calls" / f"{name}.reply.json", reply)
        _dump(self.out / "calls" / f"{name}.meta.json", {
            "stage": self.stage, "prompt_chars": len(prompt), "images": len(images or []),
            "seconds": round(time.time() - started, 1), "model": self.model})
        return reply


def prepare(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if path.suffix.lower() == ".txt":
        text = data.decode("utf-8-sig")
        return {"pageCount": 1, "tablePages": [], "picked": [0], "scanned": False,
                "text": f"[[Page 1]]\n{text}", "tableText": f"[[Page 1]]\n{text}",
                "fullText": True, "pageTexts": [f"[[Page 1]]\n{text}"], "tableImages": [], "scanImages": []}
    return prepare_protocol_input(data)


def run(source: Path, out: Path, replay: Path | None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    prepared = prepare(source)
    _dump(out / "prepared.json", {
        **{k: v for k, v in prepared.items() if k in (
            "pageCount", "tablePages", "tableSource", "hiddenPages", "picked", "selected", "scanned", "fullText")},
        "alignedTables": [{k: v for k, v in t.items() if k in ("page", "rowCounts", "colCounts", "marks")}
                          for t in prepared.get("alignedTables") or []],
        "textChars": len(prepared.get("text") or ""), "tableImages": [i["page"] for i in prepared.get("tableImages") or []],
        "symbolLegend": (prepared.get("symbolLegend") or {}).get("note"),
        "file": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    })
    _dump(out / "page_texts.json", prepared.get("pageTexts") or [])
    client = Recorder(out, None if replay else OpenAIKitClient(), replay)

    def on_stage(stage: str, status: str, detail: Any = "") -> None:
        if status == "run":
            client.stage = stage
        line = f"{time.strftime('%H:%M:%S')} {stage} {status} {detail}"
        log.append(line)
        print(line, flush=True)

    client.on_status = lambda message: on_stage(client.stage, "status", message)

    passes = {"n": 0}
    real_normalize = kit_analyze.normalize

    def recording_normalize(st: Any) -> Any:
        passes["n"] += 1
        _dump(out / "normalize" / f"{passes['n']:02d}.in.json", copy.deepcopy(st))
        result = real_normalize(st)
        _dump(out / "normalize" / f"{passes['n']:02d}.out.json", result)
        return result

    kit_analyze.normalize = recording_normalize
    try:
        result = kit_analyze.analyze_protocol(prepared, client, on_stage=on_stage)
    finally:
        kit_analyze.normalize = real_normalize
        (out / "stages.log").write_text("\n".join(log), encoding="utf-8")
    _dump(out / "result.json", result)
    if client.prompt_changes:
        _dump(out / "replay_prompt_changes.json", client.prompt_changes)
        print("replay: prompts differ from the recording for", ", ".join(client.prompt_changes))
    return result


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        raise SystemExit(2)
    replay = None
    if "--replay" in sys.argv:
        replay = Path(sys.argv[sys.argv.index("--replay") + 1])
        args = [a for a in args if Path(a) != replay]
    try:
        result = run(Path(args[0]), Path(args[1]), replay)
    except AnalysisError as error:
        print(f"FAILED {error.code}: {error}")
        raise SystemExit(1)
    model = result["model"]
    print(f"done: {len(model['visits'])} visits, {len(model['events'])} events, {len(model.get('rules') or [])} rules, "
          f"{len(model['review_questions'])} review questions, {len(model['notes'])} notes")


if __name__ == "__main__":
    main()
