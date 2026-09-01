"""Ingest the NAFDAC Greenbook (Nigeria) product register.

The Greenbook is served by a server-side DataTables endpoint. It is the richest
of the three registers in scope: it carries ATC classification, INN, strength,
dosage form, route, and (critically) applicant name, which is what lets us
isolate one company's portfolio.

Run:  python -m rri.ingest_nafdac
"""

from __future__ import annotations

import sys
import time

import httpx

from rri.provenance import write_snapshot

SOURCE_ID = "nafdac_greenbook"
DATA_URL = "https://greenbook.nafdac.gov.ng"
# 500 is the largest page the endpoint serves reliably; 1000 makes it drop the
# connection. Probed, not guessed.
PAGE_SIZE = 500
REQUEST_TIMEOUT = 90.0
PAUSE_SECONDS = 0.5  # deliberate: this is a public health authority, not a target
MAX_RETRIES = 4

HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
    "User-Agent": (
        "row-registration-intelligence/0.1 "
        "(public regulatory data research; contact via repository)"
    ),
}

# The endpoint requires at least one column declaration to respond.
BASE_PARAMS = {
    "columns[0][data]": "product_name",
    "columns[0][name]": "product_name",
    "columns[0][searchable]": "true",
    "columns[0][orderable]": "true",
    "columns[0][search][value]": "",
    "columns[0][search][regex]": "false",
    "search[value]": "",
    "search[regex]": "false",
}


def fetch_page(client: httpx.Client, start: int, length: int, draw: int) -> dict:
    """Fetch one page, retrying on transport failure.

    The endpoint drops connections intermittently. Retrying matters more here
    than it looks: a silently short read would make real registrations vanish
    from the matrix and show up downstream as whitespace that does not exist.
    """
    params = dict(BASE_PARAMS)
    params.update({"draw": str(draw), "start": str(start), "length": str(length)})

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.get(DATA_URL, params=params, headers=HEADERS,
                                  timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            backoff = 2 ** attempt
            print(f"    retry {attempt + 1}/{MAX_RETRIES} at start={start} "
                  f"({type(exc).__name__}); waiting {backoff}s", flush=True)
            time.sleep(backoff)

    raise RuntimeError(
        f"failed to fetch page at start={start} after {MAX_RETRIES} attempts"
    ) from last_error


def fetch_all(verbose: bool = True) -> tuple[list[dict], int]:
    """Page through the whole register.

    Returns (records, records_total_reported). The reported total is kept
    separate from len(records) on purpose, if they disagree, that is a
    coverage problem the caller must surface, not paper over.
    """
    records: list[dict] = []
    seen_ids: set = set()
    reported_total = 0
    draw = 1

    with httpx.Client(follow_redirects=True) as client:
        while True:
            payload = fetch_page(client, start=len(records), length=PAGE_SIZE, draw=draw)
            reported_total = payload.get("recordsTotal", 0)
            page = payload.get("data", [])
            if not page:
                break

            for row in page:
                pid = row.get("product_id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                records.append(row)

            if verbose:
                print(f"  fetched {len(records):>5} / {reported_total}", flush=True)

            if len(records) >= reported_total or len(page) < PAGE_SIZE:
                break
            draw += 1
            time.sleep(PAUSE_SECONDS)

    return records, reported_total


def main() -> int:
    print(f"Ingesting {SOURCE_ID} from {DATA_URL}")
    records, reported_total = fetch_all()

    if not records:
        print("ERROR: no records retrieved; refusing to write an empty snapshot",
              file=sys.stderr)
        return 1

    note = f"recordsTotal reported by endpoint: {reported_total}"
    if len(records) != reported_total:
        # Never silently accept a short read. A partial register would turn into
        # phantom whitespace downstream. A product would look unregistered
        # purely because we failed to fetch its row.
        note += f" | WARNING partial read: retrieved {len(records)}"
        print(f"WARNING: retrieved {len(records)} but endpoint reported "
              f"{reported_total}", file=sys.stderr)

    snap = write_snapshot(SOURCE_ID, DATA_URL, records, note=note)
    print(f"\nsnapshot   {snap.path}")
    print(f"records    {snap.record_count}")
    print(f"sha256     {snap.sha256}")
    print(f"retrieved  {snap.retrieved_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
