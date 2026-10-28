"""Minimal ArcGIS REST client: id-based paging, retries, on-disk cache.

Every public layer the screener reads is an ArcGIS FeatureServer/MapServer
or ImageServer. Paging is done by object id (returnIdsOnly, then fetch in
chunks) because offset paging double-counts or skips rows on some county
servers.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import requests

CACHE_DIR = Path(__file__).resolve().parent / "cache" / "http"
USER_AGENT = "land-screener/0.1 (internal land-acquisition research)"

_local = threading.local()


def _session() -> requests.Session:
    """One requests.Session per thread (slope checks run in a thread pool)."""
    sess = getattr(_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers["User-Agent"] = USER_AGENT
        _local.session = sess
    return sess

# Cache lifetime in days; run.py overrides it from settings.toml.
cache_days: float = 7.0


class ArcGISError(RuntimeError):
    pass


def _cache_path(url: str, params: dict[str, Any]) -> Path:
    key = hashlib.sha256(
        (url + "?" + json.dumps(params, sort_keys=True, default=str)).encode()
    ).hexdigest()
    return CACHE_DIR / key[:2] / f"{key}.json"


def request_json(url: str, params: dict[str, Any], *, retries: int = 5,
                 timeout: int = 90, use_cache: bool = True) -> dict[str, Any]:
    """POST a form request and return JSON, with retry/backoff and caching.

    POST keeps long polygon geometries out of the URL. ArcGIS error bodies
    (HTTP 200 with an "error" key) count as failures.
    """
    params = {**params, "f": params.get("f", "json")}
    path = _cache_path(url, params)
    if use_cache and cache_days > 0 and path.exists():
        if time.time() - path.stat().st_mtime < cache_days * 86400:
            return json.loads(path.read_text())

    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _session().post(url, data=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                raise ArcGISError(f"{url}: {data['error']}")
            if use_cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(path)
            return data
        except (requests.RequestException, ValueError, ArcGISError) as exc:
            last = exc
            time.sleep(min(30.0, 2.0 ** attempt) + random.random())
    raise ArcGISError(f"giving up after {retries} tries: {url} ({last})")


def _chunks(seq: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def query_ids(url: str, where: str = "1=1",
              envelope: tuple[float, float, float, float] | None = None) -> list[int]:
    """Object ids matching a where clause and optional WGS84 envelope."""
    params: dict[str, Any] = {"where": where, "returnIdsOnly": "true"}
    if envelope:
        params.update(_envelope_params(envelope))
    data = request_json(f"{url}/query", params)
    return sorted(data.get("objectIds") or [])


def query_features(url: str, where: str = "1=1", out_fields: str = "*",
                   envelope: tuple[float, float, float, float] | None = None,
                   return_geometry: bool = True, chunk: int = 250,
                   max_offset: float | None = None) -> list[dict[str, Any]]:
    """All features matching the filter, as Esri JSON dicts in WGS84."""
    ids = query_ids(url, where, envelope)
    return fetch_by_ids(url, ids, out_fields, return_geometry, chunk, max_offset)


def fetch_by_ids(url: str, ids: list[int], out_fields: str = "*",
                 return_geometry: bool = True, chunk: int = 250,
                 max_offset: float | None = None) -> list[dict[str, Any]]:
    """Features for the given object ids, as Esri JSON dicts in WGS84."""
    feats: list[dict[str, Any]] = []
    for part in _chunks(sorted(ids), chunk):
        params: dict[str, Any] = {
            "objectIds": ",".join(map(str, part)),
            "outFields": out_fields,
            "returnGeometry": "true" if return_geometry else "false",
            "outSR": 4326,
        }
        if max_offset:
            params["maxAllowableOffset"] = max_offset
        data = request_json(f"{url}/query", params)
        feats.extend(data.get("features") or [])
    return feats


def _envelope_params(env: tuple[float, float, float, float]) -> dict[str, Any]:
    xmin, ymin, xmax, ymax = env
    return {
        "geometry": json.dumps({"xmin": xmin, "ymin": ymin, "xmax": xmax,
                                "ymax": ymax, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }


def download(url: str, dest: Path, max_age_days: float = 30.0) -> Path:
    """Download a file once and reuse it until it is max_age_days old."""
    if dest.exists() and time.time() - dest.stat().st_mtime < max_age_days * 86400:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last: Exception | None = None
    for attempt in range(4):
        try:
            with _session().get(url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as fh:
                    for block in resp.iter_content(1 << 20):
                        fh.write(block)
            tmp.replace(dest)
            return dest
        except requests.RequestException as exc:
            last = exc
            time.sleep(2.0 ** attempt)
    raise ArcGISError(f"download failed: {url} ({last})")
