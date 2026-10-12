"""Record <-> document mapping for the simple (one record = one document) collections.

``id`` is stored as ``_id`` (an application UUID). Every document carries
``doc_version`` so a later layout change can be migrated deliberately. Dates are
ISO strings and instants are UTC BSON dates (see ``codec``).
"""

from __future__ import annotations

from typing import Any, TypeVar

from app.application.records import Record

from .codec import encode

DOC_VERSION = 1
R = TypeVar("R", bound=Record)


def to_document(record: Record) -> dict[str, Any]:
    data = encode(record.model_dump(mode="python"))
    data["_id"] = data.pop("id")
    data["doc_version"] = DOC_VERSION
    return data


def from_document(cls: type[R], document: dict[str, Any]) -> R:
    data = dict(document)
    data["id"] = data.pop("_id")
    data.pop("doc_version", None)
    return cls.model_validate(data)
