"""Compare the two most recent snapshots of each source and report what moved.

This is the part that makes the system a feed rather than a report. It produces
nothing on day one, by construction: with a single snapshot there is no "before"
to compare against, and inventing one would be worse than silence.

Run:  python -m rri.watch
      python -m rri.watch --source nafdac_greenbook
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from rri.diff import diff
from rri.products import ProductRecord, from_nafdac, from_tmda, parse_generic_name
from rri.provenance import Snapshot, load_manifest, load_records
from rri.sources.anvisa import AnvisaAdapter

# How each source's raw snapshot rows become canonical records. Nigeria and
# Tanzania predate the adapter contract and are mapped here until they migrate.
MAPPERS = {
    "nafdac_greenbook": lambda rows, ref: [from_nafdac(r, ref) for r in rows],
    "anvisa_medicamentos": lambda rows, ref: AnvisaAdapter().to_records(rows, ref),
    "tmda_approved_products": lambda rows, ref: [
        from_tmda({**r,
                   "marketing_authorisation_holder": None,
                   "parsed": parse_generic_name(r.get("generic_name") or "")}, ref)
        for r in rows
    ],
}

AUTHORITY = {
    "nafdac_greenbook": "NAFDAC Greenbook",
    "anvisa_medicamentos": "ANVISA",
    "tmda_approved_products": "TMDA",
}


def snapshots_by_source() -> dict[str, list[Snapshot]]:
    grouped: dict[str, list[Snapshot]] = defaultdict(list)
    for snap in load_manifest():
        grouped[snap.source_id].append(snap)
    return grouped


def records_from(snap: Snapshot) -> list[ProductRecord]:
    mapper = MAPPERS.get(snap.source_id)
    if mapper is None:
        return []
    return mapper(load_records(snap), snap.path)


def watch(source_id: str | None = None) -> int:
    grouped = snapshots_by_source()
    targets = [s for s in grouped if s in MAPPERS]
    if source_id:
        targets = [s for s in targets if s == source_id]
    if not targets:
        print("No comparable sources found.")
        return 1

    any_history = False
    for sid in sorted(targets):
        snaps = grouped[sid]
        authority = AUTHORITY.get(sid, sid)
        print(f"\n{authority}  ({sid})")

        if len(snaps) < 2:
            print(f"  only {len(snaps)} snapshot. Nothing to compare yet.")
            print("  History accrues from here; re-run after the next refresh.")
            continue

        any_history = True
        before, after = snaps[-2], snaps[-1]
        result = diff(
            records_from(before), records_from(after),
            before.retrieved_date, after.retrieved_date, authority,
        )

        print(f"  {before.retrieved_at[:19]} -> {after.retrieved_at[:19]}")
        print(f"  records {before.record_count:,} -> {after.record_count:,}"
              f"   keyed and compared: {result.compared:,}")
        if result.unkeyed_before or result.unkeyed_after:
            print(f"  not diffable (no stable identifier): "
                  f"{result.unkeyed_before:,} before, {result.unkeyed_after:,} after")

        counts = result.by_kind()
        if counts:
            for kind, n in sorted(counts.items()):
                print(f"    {kind:<16} {n:>6,}")
        else:
            print("    no changes detected")

        if result.note:
            print(f"  ⚠ {result.note}")

        for change in result.changes[:8]:
            print(f"    · [{change.kind}] {(change.company or '?')[:34]:<36} "
                  f"{change.product[:30]}")
        if len(result.changes) > 8:
            print(f"    … and {len(result.changes) - 8:,} more")

    if not any_history:
        print("\nNo source has two snapshots yet. Every trigger currently shown in "
              "the product comes from a single snapshot; change-based signals "
              "switch on once a second retrieval exists.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="limit to one source id")
    args = parser.parse_args()
    return watch(args.source)


if __name__ == "__main__":
    raise SystemExit(main())
