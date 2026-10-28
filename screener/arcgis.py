"""Minimal ArcGIS REST client: id-based paging, retries, on-disk cache.

Every public layer the screener reads is an ArcGIS FeatureServer/MapServer
or ImageServer. Paging is done by object id (returnIdsOnly, then fetch in
chunks) because offset paging double-counts or skips rows on some county
servers.

An answer that is well-formed but not what the query asks for (no id list,
fewer features than ids, no histograms) raises BadAnswer. It is never
cached, so a re-run asks again instead of reusing it.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

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
# A bad answer rarely fixes itself within seconds: ask once more, then report.
BAD_ANSWER_TRIES = 2


class ArcGISError(RuntimeError):
    pass


class BadAnswer(ArcGISError):
    """HTTP 200 and no ArcGIS error, but not the answer the query asks for."""


def _cache_path(url: str, params: dict[str, Any]) -> Path:
    key = hashlib.sha256(
        (url + "?" + json.dumps(params, sort_keys=True, default=str)).encode()
    ).hexdigest()
    return CACHE_DIR / key[:2] / f"{key}.json"


def request_json(url: str, params: dict[str, Any], *, retries: int = 5,
                 timeout: int = 90, use_cache: bool = True, fresh: bool = False,
                 check: Callable[[dict[str, Any]], str | None] | None = None) -> dict[str, Any]:
    """POST a form request and return JSON, with retry/backoff and caching.

    POST keeps long polygon geometries out of the URL. ArcGIS error bodies
    (HTTP 200 with an "error" key) and non-object bodies count as failures.
    check(answer) says what is wrong with an answer, or None if nothing:
    a failing answer is tried BAD_ANSWER_TRIES times, then raises
    BadAnswer, and is never cached or served from the cache. fresh skips
    the cached answer but caches the new one.
    """
    params = {**params, "f": params.get("f", "json")}
    path = _cache_path(url, params)
    if use_cache and not fresh and cache_days > 0 and path.exists():
        if time.time() - path.stat().st_mtime < cache_days * 86400:
            try:
                cached = json.loads(path.read_text())
            except ValueError:  # damaged cache file: ask again
                cached = None
            if isinstance(cached, dict) and not (check and check(cached)):
                return cached

    last: Exception | None = None
    bad = 0
    for attempt in range(retries):
        try:
            resp = _session().post(url, data=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ArcGISError(f"{url}: the answer is not a JSON object")
            if "error" in data:
                raise ArcGISError(f"{url}: {data['error']}")
            problem = check(data) if check else None
            if problem:
                bad += 1
                raise BadAnswer(f"{url}: {problem}")
            if use_cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps(data))
                tmp.replace(path)
            return data
        except (requests.RequestException, ValueError, ArcGISError) as exc:
            last = exc
            if bad >= BAD_ANSWER_TRIES:
                raise BadAnswer(f"{exc} (asked {bad} times)") from None
            time.sleep(min(30.0, 2.0 ** attempt) + random.random())
    raise ArcGISError(f"giving up after {retries} tries: {url} ({last})")


def _chunks(seq: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _ids_problem(data: dict[str, Any]) -> str | None:
    # No matches is objectIds null (MapServer) or [] (hosted layers); an
    # answer without the key is not "no matches".
    if "objectIds" not in data:
        return "no objectIds in the answer"
    if not isinstance(data["objectIds"], (list, type(None))):
        return "objectIds is not a list"
    if data.get("exceededTransferLimit"):
        return "the id list was cut short (exceededTransferLimit)"
    return None


def _features_problem(want: int, geometry: bool) -> Callable[[dict[str, Any]], str | None]:
    def problem(data: dict[str, Any]) -> str | None:
        feats = data.get("features")
        if not isinstance(feats, list):
            return "no features in the answer"
        if data.get("exceededTransferLimit"):
            return f"the server cut the answer short at {len(feats)} of {want} features"
        if len(feats) < want:
            return f"{len(feats)} of {want} features came back"
        sr = data.get("spatialReference")
        sr = sr if isinstance(sr, dict) else {}
        if geometry and feats and 4326 not in (sr.get("wkid"), sr.get("latestWkid")):
            return "the features did not come back in WGS84"
        return None
    return problem


def query_ids(url: str, where: str = "1=1",
              envelope: tuple[float, float, float, float] | None = None,
              fresh: bool = False) -> list[int]:
    """Object ids matching a where clause and optional WGS84 envelope."""
    params: dict[str, Any] = {"where": where, "returnIdsOnly": "true"}
    if envelope:
        params.update(_envelope_params(envelope))
    data = request_json(f"{url}/query", params, fresh=fresh, check=_ids_problem)
    return sorted(data["objectIds"] or [])


def query_features(url: str, where: str = "1=1", out_fields: str = "*",
                   envelope: tuple[float, float, float, float] | None = None,
                   return_geometry: bool = True, chunk: int = 250,
                   max_offset: float | None = None) -> list[dict[str, Any]]:
    """All features matching the filter, as Esri JSON dicts in WGS84."""
    return features_in(url, where, [envelope], out_fields, return_geometry, chunk, max_offset)


def features_in(url: str, where: str,
                envelopes: list[tuple[float, float, float, float] | None],
                out_fields: str = "*", return_geometry: bool = True, chunk: int = 250,
                max_offset: float | None = None) -> list[dict[str, Any]]:
    """Features matching the filter in any of the envelopes (None: anywhere).

    If the features still come back short, the cached id list may be stale
    (a re-published layer renumbers its ids), so the ids are asked for
    again, uncached, once before giving up.
    """
    last: BadAnswer | None = None
    for fresh in (False, True):
        ids: set[int] = set()
        for env in envelopes:
            ids.update(query_ids(url, where, env, fresh=fresh))
        try:
            return fetch_by_ids(url, sorted(ids), out_fields, return_geometry, chunk, max_offset)
        except BadAnswer as exc:
            last = exc
    raise BadAnswer(f"{last}; re-run later, or with --refresh")


def fetch_by_ids(url: str, ids: list[int], out_fields: str = "*",
                 return_geometry: bool = True, chunk: int = 250,
                 max_offset: float | None = None) -> list[dict[str, Any]]:
    """Features for the given object ids, as Esri JSON dicts in WGS84.

    A chunk with fewer features than ids (rows deleted mid-read, a server
    capping its answers) raises BadAnswer rather than dropping features.
    """
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
        data = request_json(f"{url}/query", params,
                            check=_features_problem(len(set(part)), return_geometry))
        feats.extend(data["features"])
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


def download(url: str, dest: Path, max_age_days: float = 30.0,
             check: Callable[[Path], bool] | None = None) -> Path:
    """Download a file once and reuse it until it is max_age_days old.

    check(path) says whether a file is the one expected (a zip, say, not a
    maintenance page): a download that fails it is not kept, and a cached
    file that fails it is downloaded again.
    """
    if (dest.exists() and time.time() - dest.stat().st_mtime < max_age_days * 86400
            and (check is None or check(dest))):
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
            if check is not None and not check(tmp):
                raise ArcGISError("not the expected file")
            tmp.replace(dest)
            return dest
        except (requests.RequestException, ArcGISError) as exc:
            last = exc
            time.sleep(2.0 ** attempt)
    tmp.unlink(missing_ok=True)
    raise ArcGISError(f"download failed: {url} ({last})")
