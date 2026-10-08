"""Fetch a third-party event log by DOI and verify it against the manifest.

Nothing here parses a log; this module's only job is to put bytes on disk that
provably match `logs/manifest.json`, and to say which source served them. A log
whose license does not permit redistribution is never committed, so this is the
only way to get it (see logs/README.md).

Upstream is not always healthy. As of 2026-10-07 `data.4tu.nl/ndownloader`
returns 503 with Retry-After: 3600 while 4TU storage is under maintenance. The
4TU records are figshare-backed and the figshare mirror serves normally, so each
log lists its sources in preference order and we report the one that worked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "logs" / "manifest.json"
CHUNK = 1 << 20


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())["logs"]


def digest(path: Path, algorithm: str) -> str:
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            hasher.update(block)
    return hasher.hexdigest()


def verify(path: Path, entry: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means the file is good."""
    problems: list[str] = []
    if not path.exists():
        return [f"{path} does not exist"]
    actual_size = path.stat().st_size
    expected_size = entry.get("size_bytes")
    if expected_size is not None and actual_size != expected_size:
        problems.append(f"size is {actual_size} bytes, manifest expects {expected_size}")
    for algorithm in ("sha256", "md5"):
        expected = entry.get(algorithm)
        if expected is None:
            continue
        actual = digest(path, algorithm)
        if actual != expected:
            problems.append(f"{algorithm} is {actual}, manifest expects {expected}")
    return problems


def _download(url: str, destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "matchbook/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, CHUNK)
    partial.replace(destination)


def fetch(log_id: str, *, force: bool = False) -> Path:
    manifest = load_manifest()
    if log_id not in manifest:
        raise SystemExit(
            f"unknown log {log_id!r}; manifest has: {', '.join(sorted(manifest))}"
        )
    entry = manifest[log_id]
    destination = REPO_ROOT / entry["destination"]

    if destination.exists() and not force:
        problems = verify(destination, entry)
        if not problems:
            print(f"{log_id}: already present and verified at {entry['destination']}")
            return destination
        print(f"{log_id}: present but does not match the manifest:")
        for problem in problems:
            print(f"  - {problem}")
        print("re-downloading; pass --force to skip this check next time")

    failures: list[str] = []
    for url in entry["sources"]:
        print(f"{log_id}: trying {url}")
        try:
            _download(url, destination)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "code", None) or getattr(exc, "reason", exc)
            failures.append(f"{url} -> {reason}")
            print(f"{log_id}: source failed ({reason})")
            continue
        problems = verify(destination, entry)
        if problems:
            failures.append(f"{url} -> served a file that failed verification")
            for problem in problems:
                print(f"  - {problem}")
            continue
        print(f"{log_id}: verified, from {url}")
        print(f"  license: {entry['license']}  --  cite: {entry['citation']}")
        return destination

    raise SystemExit(
        f"could not fetch {log_id} from any source.\n"
        + "\n".join(f"  {failure}" for failure in failures)
        + "\n\n4TU returns 503 while its storage is under maintenance; the figshare "
        "mirror is the fallback and is already in the manifest. If both are down, "
        "the committed snapshot in logs/snapshot/ still lets the repo run -- see "
        "logs/README.md."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", help="a log id from logs/manifest.json")
    parser.add_argument("--force", action="store_true", help="re-download even if verified")
    parser.add_argument("--list", action="store_true", help="list known logs and exit")
    args = parser.parse_args(argv)
    if args.list:
        for log_id, entry in sorted(load_manifest().items()):
            where = "committed" if entry["committed"] else "fetched"
            print(f"{log_id:10s} {entry['license']:12s} {where:9s} {entry['title']}")
        return 0
    if not args.log:
        parser.error("pass --log <id>, or --list to see the known logs")
    fetch(args.log, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
