"""Provenance: every byte this project reads is snapshotted, hashed, and dated.

The output of this project makes claims about real companies and real regulatory
authorities. Any such claim has to be traceable to the exact bytes it came from,
on the date they were retrieved, otherwise it is an assertion, not evidence.

Nothing downstream reads a live URL. Everything reads a snapshot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "data" / "snapshots"
MANIFEST_PATH = SNAPSHOT_DIR / "manifest.jsonl"


@dataclass(frozen=True)
class Snapshot:
    """A single retrieval, recorded so it can be cited and re-checked."""

    source_id: str
    url: str
    retrieved_at: str  # ISO-8601 UTC
    sha256: str
    record_count: int
    path: str  # relative to repo root
    note: str = ""

    @property
    def retrieved_date(self) -> str:
        """Date only. This is what appears in user-facing citations."""
        return self.retrieved_at[:10]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_snapshot(
    source_id: str,
    url: str,
    records: list[dict],
    note: str = "",
) -> Snapshot:
    """Persist records as a hashed, dated snapshot and append to the manifest.

    The payload is serialised with sorted keys so the hash is stable for
    identical content. That is what makes the cold-clone reproduction check
    meaningful rather than decorative.
    """
    payload = json.dumps(records, sort_keys=True, ensure_ascii=False, indent=2)
    payload_bytes = payload.encode("utf-8")
    digest = sha256_bytes(payload_bytes)
    retrieved_at = _utc_now_iso()

    out_dir = SNAPSHOT_DIR / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{retrieved_at[:10]}_{digest[:12]}.json"
    out_path.write_bytes(payload_bytes)

    snap = Snapshot(
        source_id=source_id,
        url=url,
        retrieved_at=retrieved_at,
        sha256=digest,
        record_count=len(records),
        path=str(out_path.relative_to(REPO_ROOT)),
        note=note,
    )

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(snap), sort_keys=True) + "\n")

    return snap


def load_manifest() -> list[Snapshot]:
    """Every snapshot ever taken, oldest first."""
    if not MANIFEST_PATH.exists():
        return []
    entries = []
    for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(Snapshot(**json.loads(line)))
    return entries


def latest_snapshot(source_id: str) -> Snapshot | None:
    """Most recent snapshot for a source, or None if never retrieved.

    Returning None matters: callers must be able to distinguish 'we looked and
    found nothing' from 'we never looked'. Conflating those is how a coverage
    gap turns into a false claim about a company.
    """
    matches = [s for s in load_manifest() if s.source_id == source_id]
    return matches[-1] if matches else None


def load_records(snap: Snapshot) -> list[dict]:
    return json.loads((REPO_ROOT / snap.path).read_text(encoding="utf-8"))


def verify_snapshot(snap: Snapshot) -> bool:
    """Re-hash a snapshot on disk and confirm it matches the manifest."""
    path = REPO_ROOT / snap.path
    if not path.exists():
        return False
    return sha256_bytes(path.read_bytes()) == snap.sha256
