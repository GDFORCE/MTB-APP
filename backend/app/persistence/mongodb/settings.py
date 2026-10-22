"""Connection settings for the UCTSM MongoDB database.

UCTSM has its OWN database, chosen by environment variable, so it can never
collide with the legacy collections ``server.py`` uses (``DB_NAME``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class MongoSettingsError(RuntimeError):
    pass


class MongoPostureError(MongoSettingsError):
    """The connection is configured, but not safely enough for this environment."""


@dataclass(frozen=True)
class MongoSettings:
    # repr(False): the URL may carry credentials, and a settings object ends up in tracebacks.
    url: str = field(repr=False)
    database: str = ""

    @property
    def safe_url(self) -> str:
        """Host list only: never the user, password or query string."""
        return urlparse(self.url).netloc.rsplit("@", 1)[-1]

    def enforce_environment_posture(self) -> "MongoSettings":
        environment = os.getenv("UCTSM_ENV", "").strip().lower()
        if environment in {"staging", "production"}:
            issues = self.posture_issues()
            if issues:
                raise MongoPostureError(f"MongoDB connection is not fit for {environment}: " + ", ".join(issues))
        return self

    def posture_issues(self) -> list[str]:
        """Stable codes for what makes this URL unfit for staging/production (P14-04).
        A local URL (tests, a developer machine) has no issues by definition."""
        if self.is_local:
            return []
        parsed = urlparse(self.url)
        query = {key.lower(): [value.lower() for value in values] for key, values in parse_qs(parsed.query).items()}
        issues = []
        if not (parsed.scheme == "mongodb+srv" or query.get("tls") == ["true"] or query.get("ssl") == ["true"]):
            issues.append("TLS_REQUIRED")
        keyed_auth = query.get("authmechanism", [""])[0] in {"mongodb-x509", "mongodb-aws"}
        if "@" not in parsed.netloc and not keyed_auth:
            issues.append("AUTHENTICATION_REQUIRED")
        if query.get("tlsallowinvalidcertificates") == ["true"] or query.get("tlsinsecure") == ["true"]:
            issues.append("CERTIFICATE_VALIDATION_DISABLED")
        if query.get("retrywrites") == ["false"]:
            issues.append("RETRYABLE_WRITES_DISABLED")
        if query.get("w") and query["w"] != ["majority"]:
            issues.append("WRITE_CONCERN_NOT_MAJORITY")
        return issues

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
        return cls(url=url, database=database).enforce_environment_posture()

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
            f"refusing to use a non-local MongoDB for tests: {settings.safe_url}")
    return settings
