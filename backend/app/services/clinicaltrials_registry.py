"""ClinicalTrials.gov API v2 behind the ``RegistryLookup`` port.

Public, unauthenticated, read-only GET (no credential exists or is needed). The
adapter sends ONLY the registry identifier - never a document, a patient or a
filename - with a bounded timeout, one bounded retry on a transient failure and a
short in-process cache. Every outcome is an explicit status:

* ``CONFIRMED``   the registry returned that study (title / organization recorded
                  in ``detail`` as provenance; this proves the identifier exists,
                  not that the uploaded document is that study's protocol - the
                  classifier compares the document's own facts for that);
* ``NOT_FOUND``   HTTP 404 for a well-formed NCT identifier;
* ``UNAVAILABLE`` timeout, transport error, 429/5xx after the retry;
* ``NOT_CHECKED`` the identifier is not an NCT number (this registry cannot say).

Configured with ``UCTSM_REGISTRY_PROVIDER=clinicaltrials_gov``; otherwise the app
keeps ``NoRegistry`` and reports NOT_CHECKED. No scripted client is ever passed
off as this provider: tests inject a transport and say so.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.application.protocol_identity import RegistryLookup
from app.domain.protocol_identity.models import (
    IdentifierKind, RegistryComparison, RegistryStatus,
)

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
PROVIDER = "clinicaltrials.gov/api/v2"
_NCT = re.compile(r"^NCT\d{8}$")
_TIMEOUT_SECONDS = 6.0
_CACHE_SECONDS = 15 * 60

Transport = Callable[[str, float], tuple[int, dict[str, Any] | None]]


def _httpx_transport(url: str, timeout: float) -> tuple[int, dict[str, Any] | None]:
    import httpx

    response = httpx.get(url, timeout=timeout, headers={"Accept": "application/json"})
    body: dict[str, Any] | None = None
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = None
    return response.status_code, body


class ClinicalTrialsGovRegistry:
    """Implements :class:`RegistryLookup`."""

    def __init__(
        self, transport: Transport | None = None, *, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._transport = transport or _httpx_transport
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, tuple[float, RegistryComparison]] = {}

    def lookup(self, identifier: str, *, kind: IdentifierKind | None = None) -> RegistryComparison:
        del kind                                   # the identifier shape decides
        value = (identifier or "").strip().upper()
        if not _NCT.match(value):
            return RegistryComparison(
                status=RegistryStatus.NOT_CHECKED, provider=PROVIDER, identifier=identifier,
                detail="Only NCT registry identifiers can be checked against ClinicalTrials.gov.")
        now = self._clock()
        hit = self._cache.get(value)
        if hit is not None and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
        result = self._fetch(value)
        if result.status in (RegistryStatus.CONFIRMED, RegistryStatus.NOT_FOUND):
            self._cache[value] = (now, result)       # an outage is never cached
        return result

    def _fetch(self, value: str) -> RegistryComparison:
        url = (f"{BASE_URL}/{value}"
               "?fields=protocolSection.identificationModule")
        checked_at = datetime.now(timezone.utc).isoformat()
        status_code: int | None = None
        body: dict[str, Any] | None = None
        detail = "Registry request not attempted"
        for attempt in range(2):
            try:
                status_code, body = self._transport(url, _TIMEOUT_SECONDS)
            except Exception as error:               # noqa: BLE001 - transport failure is data
                status_code = None
                detail = f"Registry request failed: {type(error).__name__}"
            else:
                detail = f"Registry answered HTTP {status_code}"
            if status_code is not None and status_code not in (429, 500, 502, 503, 504):
                break
            if attempt == 0:
                self._sleep(0.5)
        if status_code == 200 and body is not None:
            identification = ((body.get("protocolSection") or {}).get("identificationModule") or {})
            returned = str(identification.get("nctId") or "").strip().upper()
            if returned != value:
                # never record a CONFIRMED result for a record that is not the one asked for
                return RegistryComparison(
                    status=RegistryStatus.UNAVAILABLE, provider=PROVIDER, identifier=value,
                    checked_at=checked_at,
                    detail="The registry answered with a different or missing identifier; "
                           "the record was not accepted.")
            title = identification.get("briefTitle") or identification.get("officialTitle") or ""
            organization = ((identification.get("organization") or {}).get("fullName")) or ""
            acronym = identification.get("acronym") or (
                (identification.get("orgStudyIdInfo") or {}).get("id")) or ""
            return RegistryComparison(
                status=RegistryStatus.CONFIRMED, provider=PROVIDER, identifier=value,
                checked_at=checked_at, title=title or None, acronym=acronym or None,
                organization=organization or None,
                detail=f"Registry record found: {title}" + (f" ({organization})" if organization else ""))
        if status_code == 404:
            return RegistryComparison(
                status=RegistryStatus.NOT_FOUND, provider=PROVIDER, identifier=value,
                checked_at=checked_at, detail="The registry has no study with this identifier.")
        return RegistryComparison(
            status=RegistryStatus.UNAVAILABLE, provider=PROVIDER, identifier=value,
            checked_at=checked_at, detail=detail)


def registry_from_environment(env: dict[str, str] | None = None) -> RegistryLookup | None:
    import os

    source = os.environ if env is None else env
    if str(source.get("UCTSM_REGISTRY_PROVIDER", "")).strip().lower() == "clinicaltrials_gov":
        return ClinicalTrialsGovRegistry()
    return None
