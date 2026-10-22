"""P12-04: the plane's HTTP adapters never write schedule data themselves; every mutation goes through a service."""
import ast
import re
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "app" / "api"
PLANE_ADAPTERS = ["uctsm_mongo.py", "uctsm_authoring.py", "uctsm_amendments.py", "uctsm_amendment_worker.py",
                  "legacy_guard.py", "uctsm_operations.py"]
WRITE = re.compile(r"^(add|add_\w+|append|append_state|save|save_\w+|replace_\w+|transition|persist_\w+|supersede\w*|"
                   r"set_\w+|record_\w+|confirm_\w+|delete\w*|purge\w*|restore\w*|requeue\w*|withdraw|claim|settle)$")
#: The one audited exception: a refused approval is recorded in its own committed transaction (maker-checker).
ALLOWED = {("uctsm_authoring.py", "audit", "append")}


def writes(path: Path):
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and WRITE.match(node.func.attr)):
            continue
        chain, base = [], node.func.value
        while isinstance(base, ast.Attribute):
            chain.append(base.attr)
            base = base.value
        if isinstance(base, ast.Name) and base.id == "tx":
            repository = chain[-1] if chain else ""
            found.append((path.name, repository, node.func.attr, node.lineno))
    return found


def test_plane_adapters_perform_no_repository_writes_except_the_one_audited_denial_record():
    unexpected = [row for name in PLANE_ADAPTERS for row in writes(API / name)
                  if (row[0], row[1], row[2]) not in ALLOWED]
    assert unexpected == [], f"route/job adapters must call a service, not a repository: {unexpected}"


def test_the_detector_actually_finds_a_direct_write():
    sample = API.parent.parent / "tests" / "_tmp_boundary_sample.py"
    sample.write_text("def f(uow):\n    uow.run(lambda tx: tx.evaluations.save_evaluation(1, 2, 3))\n"
                      "    uow.run(lambda tx: tx.patients.save(1))\n    uow.read(lambda tx: tx.patients.get(1))\n",
                      encoding="utf-8")
    try:
        assert [(r[1], r[2]) for r in writes(sample)] == [("evaluations", "save_evaluation"), ("patients", "save")]
    finally:
        sample.unlink()
