"""Connection settings for the UCTSM MongoDB database.

UCTSM has its OWN database, chosen by environment variable, so it can never
collide with the legacy collections ``server.py`` uses (``DB_NAME``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class MongoSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class MongoSettings:
    url: str
    database: str

    @classmethod
    def from_env(cls) -> "MongoSettings":
        url = os.getenv("UCTSM_MONGO_URL", "").strip()
        database = os.getenv("UCTSM_MONGO_DB", "").strip()
        if not url or not database:
            raise MongoSettingsError(
                "UCTSM_MONGO_URL and UCTSM_MONGO_DB must be configured for UCTSM persistence")
        legacy = os.getenv("DB_NAME", "").strip()
        if legacy and database == legacy:
            raise MongoSettingsError(
                "UCTSM_MONGO_DB must differ from the legacy DB_NAME; UCTSM never shares a database "
                "with the legacy scheduling collections")
        return cls(url=url, database=database)

    @property
    def is_local(self) -> bool:
        hosts = [item for item in urlparse(self.url).netloc.rsplit("@", 1)[-1].split(",")]
        names = {host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
                 for host in hosts}
        return bool(names) and names <= _LOCAL_HOSTS


def require_local(settings: MongoSettings) -> MongoSettings:
    """Tests must never touch a shared cluster."""
    if not settings.is_local:
        raise MongoSettingsError(
            f"refusing to use a non-local MongoDB for tests: {settings.url.split('@')[-1]}")
    return settings
