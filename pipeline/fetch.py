"""Download source files into an immutable, content-addressed local store.

Files land in data/raw/FY{year}/{exhibit}/{name}.{sha8}{ext} and are never overwritten.
A manifest.json per directory remembers which URL resolved to which file, so a re-run
reuses local copies unless refresh=True.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pymupdf
import requests

from .config import SourceList, normalize_exhibit, raw_dir


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class FetchedFile:
    role: str
    format: str
    url: str
    sha256: str
    path: Path
    fetched_at: datetime
    page_count: int | None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_count(path: Path, fmt: str) -> int | None:
    if fmt != "pdf":
        return None
    with pymupdf.open(path) as doc:
        return doc.page_count


def _download(url: str, dest_dir: Path) -> Path:
    tmp = tempfile.NamedTemporaryFile(dir=dest_dir, delete=False, suffix=".part")
    try:
        with requests.get(url, stream=True, timeout=120) as resp:
            if resp.status_code != 200:
                raise FetchError(f"GET {url} returned HTTP {resp.status_code}")
            for chunk in resp.iter_content(1 << 20):
                tmp.write(chunk)
        tmp.close()
        return Path(tmp.name)
    except BaseException:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise


def fetch_sources(sources: SourceList, exhibit: str, refresh: bool = False) -> list[FetchedFile]:
    exhibit = normalize_exhibit(exhibit)
    dest_dir = raw_dir() / f"FY{sources.fiscal_year}" / exhibit
    dest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = dest_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    fetched = []
    for src in sources.files:
        entry = manifest.get(src.url)
        cached = dest_dir / entry["file"] if entry else None
        if cached and cached.exists() and not refresh:
            sha = _sha256(cached)
            if sha != entry["sha256"]:
                raise FetchError(f"{cached} does not match its recorded sha256; refusing to use it")
            fetched.append(
                FetchedFile(
                    src.role, src.format, src.url, sha, cached,
                    datetime.fromisoformat(entry["fetched_at"]), _page_count(cached, src.format),
                )
            )
            continue

        tmp = _download(src.url, dest_dir)
        sha = _sha256(tmp)
        name = Path(urlparse(src.url).path).name
        final = dest_dir / f"{Path(name).stem}.{sha[:8]}{Path(name).suffix}"
        if final.exists():
            tmp.unlink()  # identical content already stored
        else:
            tmp.rename(final)
        now = datetime.now(timezone.utc)
        manifest[src.url] = {"file": final.name, "sha256": sha, "fetched_at": now.isoformat()}
        fetched.append(
            FetchedFile(src.role, src.format, src.url, sha, final, now, _page_count(final, src.format))
        )

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return fetched
