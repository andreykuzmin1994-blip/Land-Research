"""Reference copy of zoning_import.ZoningSession at commit 4fe8080 (the PR 38
merge), before D1-11 moved it onto rules_scrape.PoliteFetcher.

Used ONLY by the differential test in tests/test_zoning_import.py
(reviews/32_zoning_import_fetcher, ZI-04): the new session and this one
drive zoning_import.run() over the same fixture world, and the UPDATEs,
exit codes and stdout must agree. The constants and the class body below
are copied verbatim from `git show 4fe8080:zoning_import.py` (lines 91-96
and 308-373); only SourceError is taken from the current module, so run()
catches it. Never imported by pipeline code; not a test module itself.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import requests

from zoning_import import SourceError

USER_AGENT = "Land-Research-ZoningImport/1.0 (between-runs operator tool; 1 req/s per host)"
REQUEST_INTERVAL_S = 1.0          # SR-11
MAX_RETRIES = 3
BACKOFF_S = (2.0, 4.0, 8.0)
RETRY_AFTER_CAP_S = 30.0
HTTP_TIMEOUT_S = 60


class ZoningSession:
    """GET-only JSON session: 1 req/s per host, bounded retries, no cache."""

    def __init__(self, http: Any = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.http = http or requests.Session()
        if hasattr(self.http, "headers"):
            self.http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.clock = clock
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.requests: dict[str, int] = {}

    def _space(self, host: str) -> None:
        with self._lock:
            now = self.clock()
            last = self._last.get(host)
            wait = 0.0 if last is None else max(0.0, REQUEST_INTERVAL_S - (now - last))
            self._last[host] = now + wait
        if wait > 0:
            self.sleep(wait)

    @staticmethod
    def retry_after(resp: Any, scheduled: float) -> float:
        try:
            ra = float(int(resp.headers.get("Retry-After", "")))
        except (TypeError, ValueError, AttributeError):
            return scheduled
        return max(scheduled, min(ra, RETRY_AFTER_CAP_S)) if ra > 0 else scheduled

    def get_json(self, url: str, params: Mapping[str, Any]) -> dict[str, Any]:
        host = urlparse(url).hostname or "?"
        status: Any = "no answer"
        for attempt in range(MAX_RETRIES + 1):
            self._space(host)
            self.requests[host] = self.requests.get(host, 0) + 1
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            try:
                resp = self.http.get(url, params=dict(params), timeout=HTTP_TIMEOUT_S)
            except (requests.ConnectionError, requests.Timeout):
                status = "connection error"
            else:
                status = resp.status_code
                if 200 <= status < 300:
                    try:
                        body = resp.json()
                    except ValueError:
                        raise SourceError(f"{host}: answer is not JSON") from None
                    if not isinstance(body, dict):
                        raise SourceError(f"{host}: answer is not a JSON object")
                    if "error" in body:
                        code = (body.get("error") or {}).get("code") if isinstance(
                            body.get("error"), dict) else None
                        raise SourceError(f"{host}: service error {code if isinstance(code, int) else '?'}")
                    return body
                if status == 429:
                    delay = self.retry_after(resp, delay)
                elif not 500 <= status < 600:
                    raise SourceError(f"{host}: HTTP {status}")
            if attempt < MAX_RETRIES:
                self.sleep(delay)
        raise SourceError(f"{host}: {status} after {MAX_RETRIES + 1} attempts")

    def total_requests(self) -> int:
        return sum(self.requests.values())
