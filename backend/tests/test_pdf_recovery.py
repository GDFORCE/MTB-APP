from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from bson import Binary, Decimal128
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pytest

from app.persistence.mongodb.recovery import canonical, checksum, native, portable, restore_isolated, seal, snapshot, unseal, verify
from _p6_world import World

pytest_plugins = ["tests.contract.conftest"]


def test_portable_tags_preserve_identity_dates_instants_binary_decimal_and_reserved_user_keys():
    original = {"id": uuid4(), "date_only": date(2026, 1, 31).isoformat(),
        "instant": datetime(2026, 1, 31, 9, 1, tzinfo=timezone.utc), "binary": Binary(b"synthetic", 128),
        "decimal": Decimal128(Decimal("1.23")), "user_value": {"@uuid": "literal user data"}}
    encoded = portable(original)
    assert native(encoded) == original
    assert b"2026-01-31" in canonical(encoded)
    with pytest.raises(ValueError): portable({"instant": datetime(2026, 1, 1)})


def test_real_mongo_snapshot_encryption_and_isolated_restore_reconcile_every_document(mongo_uow, tmp_path):
    world = World(mongo_uow, reviewed=True)
    source = mongo_uow._database
    payload, before = snapshot(source)
    verify(payload)
    key = AESGCM.generate_key(bit_length=256); path = tmp_path / "snapshot.enc"
    seal(payload, key, path)
    assert b"patient_code" not in path.read_bytes()
    assert unseal(path, key) == payload
    target = source.client[f"uctsm_restore_{uuid4().hex}"]
    try:
        report = restore_isolated(target, payload)
        assert report["status"] == "VERIFIED_ISOLATED_RESTORE"
        assert report["documents"] == before["documents"]
        assert report["source_manifest_hash"] == payload["manifest_hash"]
        assert source.patients.count_documents({}) == target.patients.count_documents({})
        with pytest.raises(ValueError, match="not empty"): restore_isolated(target, payload)
    finally:
        source.client.drop_database(target.name)


def test_wrong_key_tampering_size_limit_and_overwrite_fail(mongo_uow, tmp_path):
    World(mongo_uow)
    payload, _ = snapshot(mongo_uow._database)
    path = tmp_path / "one.enc"; key = AESGCM.generate_key(bit_length=256)
    seal(payload, key, path)
    with pytest.raises(FileExistsError): seal(payload, key, path)
    with pytest.raises(InvalidTag): unseal(path, AESGCM.generate_key(bit_length=256))
    damaged = bytearray(path.read_bytes()); damaged[-1] ^= 1
    other = tmp_path / "damaged.enc"; other.write_bytes(damaged)
    with pytest.raises(InvalidTag): unseal(other, key)
    with pytest.raises(ValueError, match="byte limit"): snapshot(mongo_uow._database, max_bytes=1)


def test_manifest_and_orphan_detection_precede_any_restore_write(mongo_uow):
    World(mongo_uow)
    source = mongo_uow._database; payload, _ = snapshot(source)
    with pytest.raises(ValueError, match="isolated"): restore_isolated(source, payload)
    collection = payload["collections"]["patient_events"]
    assert collection["documents"]
    document = native(collection["documents"][0]); document["patient_id"] = uuid4()
    collection["documents"][0] = portable(document)
    collection["sha256"] = checksum(collection["documents"])
    payload["manifest_hash"] = checksum({key: value for key, value in payload.items() if key != "manifest_hash"})
    with pytest.raises(ValueError, match="orphan"): verify(payload)
