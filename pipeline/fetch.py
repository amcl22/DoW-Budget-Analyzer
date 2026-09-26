"""Download source files into an immutable, content-addressed local store.

Files land in data/raw/FY{year}/{exhibit}/{name}.{sha8}{ext} and are never overwritten.
A manifest.json per directory remembers which URL resolved to which file, so a re-run
reuses local copies unless refresh=True.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pymupdf
import requests

from .config import ROOT, SourceFile, SourceList, normalize_exhibit, raw_dir

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


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


DOWNLOAD_TIMEOUT = (30, 120)     # connect, per-read seconds: a stalled transfer fails fast
DOWNLOAD_ATTEMPTS = 3


def _download_once(url: str, dest_dir: Path) -> Path:
    tmp = tempfile.NamedTemporaryFile(dir=dest_dir, delete=False, suffix=".part")
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": USER_AGENT}) as resp:
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


def _download(url: str, dest_dir: Path) -> Path:
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            return _download_once(url, dest_dir)
        except requests.RequestException as e:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise FetchError(f"GET {url} failed after {attempt} attempts: {e}") from e
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


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


def manual_dir(fiscal_year: int, exhibit: str) -> Path:
    return ROOT / "data" / "manual" / f"FY{fiscal_year}" / exhibit


def fetch_books(books, fiscal_year: int, exhibit: str, refresh: bool = False) -> tuple[list[FetchedFile], list[str]]:
    """Download justification books into the store; `manual` books are read from
    data/manual/FY{FY}/{exhibit}/<file name from the URL> instead. Returns (files, notes)."""
    notes: list[str] = []
    fetched: list[FetchedFile] = []
    for b in books:
        if not b.manual:
            # one at a time, so one failed download costs that book only
            try:
                fetched += fetch_sources(
                    SourceList(fiscal_year, "", [SourceFile("justification_book", "pdf", b.url)]),
                    exhibit, refresh=refresh,
                )
            except (FetchError, requests.RequestException, OSError) as e:
                notes.append(f"{b.service}: {b.url} not fetched ({e})")
            continue
        local = manual_dir(fiscal_year, exhibit) / Path(urlparse(b.url).path).name
        if not local.exists():
            notes.append(f"{b.service}: manual book not found at {local.relative_to(ROOT)}")
            continue
        fetched.append(FetchedFile("justification_book", "pdf", b.url, _sha256(local), local,
                                   datetime.fromtimestamp(local.stat().st_mtime, timezone.utc),
                                   _page_count(local, "pdf")))
    return fetched, notes
