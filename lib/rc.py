"""RingCentral API client with JWT auth, pagination, rate-limit retries."""
import base64
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config

log = logging.getLogger("rc")


class RingCentralClient:
    """
    Minimal RingCentral client with JWT bearer authentication.
    - Auto-refreshes access token when expired.
    - Retries on 429 (rate limit) respecting Retry-After.
    - Paginates /call-log automatically.
    - Throttles /recording/../content downloads (10/min limit).
    """

    # Recording downloads are rate-limited to ~10/min. Sleep 7s between calls.
    RECORDING_THROTTLE_SECONDS = 7

    def __init__(self):
        config.validate()
        self.server = config.RC_SERVER
        self.access_token = None
        self.token_expires_at = 0
        self.session = requests.Session()
        self._last_recording_fetch = 0

    # ─── auth ──────────────────────────────────────────────────────────────
    def _authenticate(self):
        creds = f"{config.RC_CLIENT_ID}:{config.RC_CLIENT_SECRET}".encode()
        auth_header = base64.b64encode(creds).decode()
        r = self.session.post(
            f"{self.server}/restapi/oauth/token",
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": config.RC_JWT,
            },
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"RC auth failed [{r.status_code}]: {r.text}")
        payload = r.json()
        self.access_token = payload["access_token"]
        self.token_expires_at = time.time() + payload.get("expires_in", 3600) - 60
        log.debug("RC authenticated; scope=%s", payload.get("scope"))

    def _ensure_token(self):
        if not self.access_token or time.time() >= self.token_expires_at:
            self._authenticate()

    def _request(self, method, path, **kwargs):
        self._ensure_token()
        url = f"{self.server}{path}" if path.startswith("/") else path
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"

        for attempt in range(5):
            r = self.session.request(
                method, url, headers=headers, timeout=kwargs.pop("timeout", 60),
                **kwargs
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "10"))
                log.warning("429 rate-limited; sleeping %ds", wait)
                time.sleep(wait)
                continue
            if r.status_code == 401:
                self._authenticate()
                headers["Authorization"] = f"Bearer {self.access_token}"
                continue
            if r.status_code >= 500:
                wait = 2 ** attempt
                log.warning("server %d; retrying in %ds", r.status_code, wait)
                time.sleep(wait)
                continue
            return r
        r.raise_for_status()
        return r

    # ─── call log ──────────────────────────────────────────────────────────
    def iter_call_log(self, date_from: datetime, date_to: datetime):
        """
        Yield call records. Uses view=Detailed. Auto-paginates.
        Chunks by 7-day windows to avoid hitting single-query limits.
        """
        cur = date_from
        chunk = timedelta(days=7)
        while cur < date_to:
            chunk_end = min(cur + chunk, date_to)
            yield from self._iter_call_log_chunk(cur, chunk_end)
            cur = chunk_end

    def _iter_call_log_chunk(self, date_from, date_to):
        page = 1
        while True:
            r = self._request(
                "GET",
                "/restapi/v1.0/account/~/call-log",
                params={
                    "dateFrom": _iso_z(date_from),
                    "dateTo": _iso_z(date_to),
                    "view": "Detailed",
                    "perPage": 1000,
                    "page": page,
                    "withRecording": False,
                },
                timeout=90,
            )
            r.raise_for_status()
            payload = r.json()
            records = payload.get("records", [])
            for rec in records:
                yield rec
            nav = payload.get("navigation", {}) or {}
            if not nav.get("nextPage") or not records:
                break
            page += 1
            time.sleep(0.3)

    # ─── recordings ────────────────────────────────────────────────────────
    def download_recording(self, recording_id: str) -> bytes:
        """
        Download MP3 content. Throttles to stay under 10/min limit.
        """
        # Enforce minimum spacing between recording requests
        elapsed = time.time() - self._last_recording_fetch
        if elapsed < self.RECORDING_THROTTLE_SECONDS:
            time.sleep(self.RECORDING_THROTTLE_SECONDS - elapsed)

        r = self._request(
            "GET",
            f"/restapi/v1.0/account/~/recording/{recording_id}/content",
            timeout=120,
        )
        self._last_recording_fetch = time.time()
        if r.status_code != 200:
            raise RuntimeError(f"Recording download failed [{r.status_code}]")
        return r.content


def _iso_z(dt: datetime) -> str:
    """ISO string ending with Z (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
