"""Bounded encrypted logical snapshots and non-destructive isolated restore drills.

This operator tool is not a substitute for hosting-provider PITR or object-store
backups. It never switches the application's configured database or deletes data.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import UUID

from bson import Binary, Decimal128, ObjectId
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

FORMAT = "uctsm-recovery.v1"
MAGIC = b"MTB-UCTSM-RECOVERY-1\n"
MAX_BYTES = 16 * 1024 * 1024
TAGS = {"@uuid", "@instant", "@bytes", "@decimal", "@oid", "@map"}


def portable(value):
    if isinstance(value, UUID): return {"@uuid": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None: raise ValueError("snapshot contains a naive instant")
        return {"@instant": value.astimezone(timezone.utc).isoformat()}
    if isinstance(value, ObjectId): return {"@oid": str(value)}  # infrastructure migration ledger only
    if isinstance(value, (Decimal, Decimal128)): return {"@decimal": str(value)}
    if isinstance(value, (Binary, bytes)):
        return {"@bytes": [base64.b64encode(bytes(value)).decode(), getattr(value, "subtype", 0)]}
    if isinstance(value, dict):
        mapped = {key: portable(item) for key, item in value.items()}
        return {"@map": mapped} if set(mapped) & TAGS else mapped
    if isinstance(value, (list, tuple)): return [portable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)): return value
    raise ValueError("snapshot contains an unsupported document type")


def native(value):
    if isinstance(value, list): return [native(item) for item in value]
    if not isinstance(value, dict): return value
    if set(value) == {"@uuid"}: return UUID(value["@uuid"])
    if set(value) == {"@instant"}:
        result = datetime.fromisoformat(value["@instant"])
        if result.tzinfo is None: raise ValueError("invalid snapshot instant")
        return result.astimezone(timezone.utc)
    if set(value) == {"@oid"}: return ObjectId(value["@oid"])
    if set(value) == {"@decimal"}: return Decimal128(value["@decimal"])
    if set(value) == {"@bytes"}:
        payload, subtype = value["@bytes"]
        return Binary(base64.b64decode(payload, validate=True), subtype)
    if set(value) == {"@map"}: return {key: native(item) for key, item in value["@map"].items()}
    if set(value) & TAGS: raise ValueError("invalid tagged snapshot value")
    return {key: native(item) for key, item in value.items()}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def checksum(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def catalog(database):
    entries = {}
    for row in database.list_collections():
        name = row["name"]
        if name.startswith("system."): continue
        if row.get("type") != "collection" or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("unsupported collection or view in snapshot")
        options = row.get("options", {})
        if set(options) - {"validator", "validationLevel", "validationAction"}:
            raise ValueError("collection options require a hosting-provider backup")
        indexes = []
        for index in database[name].list_indexes():
            spec = {key: value for key, value in index.items() if key not in {"v", "ns", "key"}}
            spec["key"] = [list(pair) for pair in index["key"].items()]
            indexes.append(spec)
        entries[name] = {"options": portable(options), "indexes": portable(sorted(indexes, key=lambda row: row["name"]))}
    return dict(sorted(entries.items()))


def snapshot(database, *, max_bytes=MAX_BYTES):
    start = time.monotonic(); structure = catalog(database)
    if database["_schema_migrations"].count_documents({"status": {"$ne": "COMPLETED"}}):
        raise ValueError("finish migrations before taking a logical snapshot")
    def read(session):
        rows = {}; size = 0
        for name, metadata in structure.items():
            documents = []
            for document in database[name].find({}, session=session).sort("_id", 1):
                value = portable(document); size += len(canonical(value))
                if size > max_bytes: raise ValueError("logical snapshot exceeds its configured byte limit")
                documents.append(value)
            rows[name] = {**metadata, "documents": documents, "count": len(documents), "sha256": checksum(documents)}
        return rows
    with database.client.start_session() as session:
        collections = session.with_transaction(read, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"), read_preference=ReadPreference.PRIMARY)
    if structure != catalog(database) or database["_schema_migrations"].count_documents({"status": {"$ne": "COMPLETED"}}):
        raise ValueError("schema changed during the snapshot; retry after migration is complete")
    payload = {"format": FORMAT, "created_at": datetime.now(timezone.utc).isoformat(),
               "collections": collections, "catalog_hash": checksum(structure)}
    payload["manifest_hash"] = checksum(payload)
    if len(canonical(payload)) > max_bytes: raise ValueError("logical snapshot exceeds its configured byte limit")
    return payload, {"snapshot_seconds": round(time.monotonic() - start, 3),
                     "collections": len(collections), "documents": sum(row["count"] for row in collections.values()),
                     "manifest_hash": payload["manifest_hash"], "created_at": payload["created_at"]}


def verify(payload):
    if set(payload) != {"format", "created_at", "collections", "catalog_hash", "manifest_hash"} or payload["format"] != FORMAT:
        raise ValueError("unknown snapshot format")
    if payload["manifest_hash"] != checksum({key: value for key, value in payload.items() if key != "manifest_hash"}):
        raise ValueError("snapshot manifest hash mismatch")
    structure = {}
    for name, row in payload["collections"].items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name): raise ValueError("invalid snapshot collection")
        if row["count"] != len(row["documents"]) or row["sha256"] != checksum(row["documents"]):
            raise ValueError("snapshot document hash or count mismatch")
        structure[name] = {"options": row["options"], "indexes": row["indexes"]}
        for document in row["documents"]: native(document)
    if checksum(structure) != payload["catalog_hash"]: raise ValueError("snapshot catalog mismatch")
    verify_references(payload)


def verify_references(payload):
    """Check declared canonical references and tenant/patient/version ownership."""
    documents = {name: {row["_id"]: row for row in (native(value) for value in data["documents"])}
                 for name, data in payload["collections"].items()}
    rules = {
        "protocols": {"trial_id": "trials"},
        "protocol_versions": {"protocol_id": "protocols", "trial_id": "trials"},
        "schedule_definitions": {"protocol_version_id": "protocol_versions", "trial_id": "trials"},
        "schedule_versions": {"protocol_version_id": "protocol_versions", "trial_id": "trials",
            "schedule_definition_id": "schedule_definitions", "based_on_schedule_version_id": "schedule_versions"},
        "schedule_events": {"schedule_version_id": "schedule_versions"},
        "schedule_evidence": {"schedule_version_id": "schedule_versions"},
        "schedule_claims": {"schedule_version_id": "schedule_versions", "evidence_id": "schedule_evidence"},
        "patients": {"trial_id": "trials", "current_schedule_version_id": "schedule_versions"},
        "schedule_evaluations": {"patient_id": "patients", "schedule_version_id": "schedule_versions"},
        "patient_events": {"patient_id": "patients", "evaluation_id": "schedule_evaluations",
            "schedule_version_id": "schedule_versions", "event_definition_id": "schedule_events"},
        "patient_schedules": {"patient_id": "patients", "current_evaluation_id": "schedule_evaluations",
            "schedule_version_id": "schedule_versions"},
        "patient_event_occurrences": {"patient_id": "patients", "schedule_version_id": "schedule_versions"},
        "patient_commands": {"patient_id": "patients", "schedule_version_id": "schedule_versions"},
        "compiled_schedule_artifacts": {"schedule_version_id": "schedule_versions"},
    }
    for collection, fields in rules.items():
        for document in documents.get(collection, {}).values():
            for field, target_collection in fields.items():
                identifier = document.get(field)
                if identifier is None: continue
                parent = documents.get(target_collection, {}).get(identifier)
                if parent is None: raise ValueError(f"snapshot orphan reference: {collection}.{field}")
                for scope in ("organization_id", "patient_id", "schedule_version_id"):
                    if document.get(scope) is not None and parent.get(scope) is not None and document[scope] != parent[scope]:
                        raise ValueError(f"snapshot ownership mismatch: {collection}.{field}")


def seal(payload, key, path: Path):
    verify(payload)
    if len(key) != 32: raise ValueError("a 256-bit managed recovery key is required")
    plain = canonical(payload)
    if len(plain) > MAX_BYTES: raise ValueError("snapshot exceeds the recovery tool byte limit")
    nonce = os.urandom(12)
    encrypted = MAGIC + nonce + AESGCM(key).encrypt(nonce, plain, MAGIC)
    # Never overwrite a previous backup, even if it has the same filename.
    with Path(path).open("xb") as output:
        output.write(encrypted); output.flush(); os.fsync(output.fileno())
    return hashlib.sha256(encrypted).hexdigest()


def unseal(path: Path, key):
    if Path(path).stat().st_size > MAX_BYTES + 1024: raise ValueError("recovery input exceeds the byte limit")
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC): raise ValueError("unknown encrypted snapshot format")
    offset = len(MAGIC); nonce = data[offset:offset + 12]
    plain = AESGCM(key).decrypt(nonce, data[offset + 12:], MAGIC)
    payload = json.loads(plain); verify(payload)
    return payload


def restore_isolated(database, payload):
    """Restore ONLY into a new isolated destination, never overwrite or repoint."""
    verify(payload)
    if not re.fullmatch(r"uctsm_restore_[a-z0-9_]{6,80}", database.name):
        raise ValueError("restore destination must be an isolated uctsm_restore_ database")
    if database.list_collection_names(): raise ValueError("restore destination is not empty")
    start = time.monotonic()
    for name, row in payload["collections"].items():
        options = native(row["options"])
        if set(options) - {"validator", "validationLevel", "validationAction"}: raise ValueError("unsupported restore options")
        database.create_collection(name, **options)
        documents = [native(document) for document in row["documents"]]
        for begin in range(0, len(documents), 200): database[name].insert_many(documents[begin:begin + 200], ordered=True)
        for index in native(row["indexes"]):
            if index["name"] == "_id_": continue
            spec = dict(index); keys = [tuple(pair) for pair in spec.pop("key")]
            database[name].create_index(keys, **spec)
    # A failure leaves an isolated, visibly incomplete destination. No application
    # setting or active database is changed; the operator can inspect it safely.
    recovered, report = snapshot(database)
    for name, row in payload["collections"].items():
        if recovered["collections"][name] != row: raise ValueError("restore reconciliation failed")
    verify_references(recovered)
    report["restore_seconds"] = round(time.monotonic() - start, 3)
    report["source_manifest_hash"] = payload["manifest_hash"]
    report["status"] = "VERIFIED_ISOLATED_RESTORE"
    return report
