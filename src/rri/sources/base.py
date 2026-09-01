"""The contract every register adapter implements.

Adding a market must be cheap, or the whole premise of this project is false.
Each regulator publishes differently. A JSON endpoint, a pile of PDFs, a
Portuguese CSV, an Angular app. And the only way that stays tractable is if the
per-market work is confined to one class implementing one interface.

An adapter has three jobs and no others:

  fetch()      get the raw records, and snapshot them with a hash and a date
  to_records() map raw fields onto ProductRecord
  coverage()   state honestly what the source does and does not contain

Everything downstream (matching, triggers, whitespace, reports) works on
ProductRecord and never knows which register a row came from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from rri.products import ProductRecord
from rri.provenance import Snapshot, latest_snapshot, load_records, write_snapshot
from rri.whitespace import SourceCoverage


class RegisterAdapter(ABC):
    """One national medicines register."""

    #: Stable identifier used for snapshot paths and the manifest.
    source_id: str
    #: ISO 3166-1 alpha-2.
    country: str
    #: Human-readable authority name, as it should appear in output.
    authority: str
    #: Where a human can verify this themselves.
    landing_url: str
    #: TLS trust store for this source. Some regulators serve an incomplete
    #: certificate chain. ANVISA sends only its leaf and omits the Sectigo
    #: intermediate. Which browsers and curl paper over but a strict client
    #: will not. The fix is to supply the missing intermediate, never to
    #: disable verification: this pipeline's whole claim is that its data is
    #: traceable to a source, and an unauthenticated fetch cannot support that.
    verify: str | bool = True

    # ---- required of every adapter -------------------------------------

    @abstractmethod
    def fetch(self) -> list[dict]:
        """Retrieve raw records from the source.

        Implementations should be resilient to partial reads and must never
        return a silently truncated set. A short read becomes phantom
        whitespace and phantom triggers downstream.
        """

    @abstractmethod
    def to_records(self, raw: list[dict], source_ref: str) -> list[ProductRecord]:
        """Map raw source rows onto the canonical record."""

    @abstractmethod
    def coverage(self, records: list[ProductRecord], snap: Snapshot) -> SourceCoverage:
        """Describe what this source contains, including what it omits.

        The limitation string is not decoration. A reader who does not know that
        a source is a published subset, or that it stops in 2023, cannot
        correctly interpret anything derived from it.
        """

    # ---- provided ------------------------------------------------------

    def snapshot(self, raw: list[dict], note: str = "") -> Snapshot:
        """Persist a fetch as a hashed, dated snapshot."""
        return write_snapshot(self.source_id, self.landing_url, raw, note=note)

    def latest(self) -> Snapshot | None:
        """Most recent snapshot for this source, or None if never fetched.

        None means "never looked", which is different from "looked and found
        nothing". Callers must be able to tell those apart.
        """
        return latest_snapshot(self.source_id)

    def load(self) -> tuple[list[ProductRecord], SourceCoverage] | None:
        """Records and coverage from the most recent snapshot."""
        snap = self.latest()
        if snap is None:
            return None
        raw = load_records(snap)
        records = self.to_records(raw, snap.path)
        return records, self.coverage(records, snap)

    def refresh(self, verbose: bool = True) -> Snapshot:
        """Fetch and snapshot. The entry point for a scheduled update."""
        raw = self.fetch()
        if not raw:
            raise RuntimeError(
                f"{self.source_id}: fetch returned nothing; refusing to write an "
                f"empty snapshot over a good one"
            )
        snap = self.snapshot(raw)
        if verbose:
            print(f"{self.source_id}: {snap.record_count:,} records "
                  f"-> {snap.path} ({snap.sha256[:12]})")
        return snap

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.country}/{self.authority}>"
