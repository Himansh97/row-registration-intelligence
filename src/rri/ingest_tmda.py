"""Ingest the TMDA (Tanzania) approved-product register.

Unlike NAFDAC, TMDA publishes no structured endpoint. It publishes a listing of
approved products, each linking to an SmPC or public assessment report PDF. The
marketing authorisation holder. The field that makes company-level analysis
possible at all, exists only inside those documents.

This module does the two mechanical stages:

  stage 1  parse the listing        -> product name, generic name, document URL
  stage 2  cache the documents      -> local PDFs, resumable

Stage 3 (extracting MAH and registration number from the documents) lives in
`extract_tmda.py`, because it is a different kind of problem and needs its own
verification.

COVERAGE LIMIT: TMDA publishes SmPCs/TPARs for *selected* registered products.
This listing is a published subset, not the complete Tanzanian register. Absence
from it is never evidence that a product is unregistered in Tanzania.

Run:  python -m rri.ingest_tmda
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

from rri.provenance import REPO_ROOT, write_snapshot

SOURCE_ID = "tmda_approved_products"
LISTING_URL = "https://www.tmda.go.tz/pages/approved-product-information"
PDF_CACHE = REPO_ROOT / "data" / "raw" / "tmda_pdfs"
REQUEST_TIMEOUT = 60.0
PAUSE_SECONDS = 0.3
MAX_RETRIES = 3

HEADERS = {
    "User-Agent": (
        "row-registration-intelligence/0.1 "
        "(public regulatory data research; contact via repository)"
    )
}

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.I)
TAG_RE = re.compile(r"<[^>]*>")


def _clean(html_fragment: str) -> str:
    text = TAG_RE.sub("", html_fragment)
    text = text.replace("&amp;", "&").replace("&#039;", "'").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_listing(html: str) -> list[dict]:
    """Pull product rows out of the listing page.

    Rows carry: product (brand) name, generic name, which embeds strength and
    dosage form, e.g. "Desloratadine 5 mg film coated tablets", and a link to
    the product's SmPC or assessment report.
    """
    products: list[dict] = []
    seen_urls: set[str] = set()

    for row_html in ROW_RE.findall(html):
        cells = CELL_RE.findall(row_html)
        if len(cells) < 3:
            continue

        product_name = _clean(cells[1])
        generic_name = _clean(cells[2])
        if not product_name or product_name.lower() in {"product name", "sn"}:
            continue

        hrefs = [h for h in HREF_RE.findall(row_html) if h.lower().endswith(".pdf")]
        doc_url = hrefs[0] if hrefs else None
        if doc_url and doc_url in seen_urls:
            continue
        if doc_url:
            seen_urls.add(doc_url)

        products.append({
            "product_name": product_name,
            "generic_name": generic_name,
            "document_url": doc_url,
        })

    return products


def fetch_listing() -> str:
    with httpx.Client(follow_redirects=True) as client:
        response = client.get(LISTING_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text


def cache_documents(products: list[dict], verbose: bool = True) -> dict[str, int]:
    """Download each product document once. Resumable: existing files are kept.

    Returns counts so a partial cache is visible rather than silent, downstream
    coverage claims depend on knowing exactly how many documents were obtained.
    """
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    stats = {"cached": 0, "downloaded": 0, "failed": 0, "no_url": 0}
    targets = [p for p in products if p.get("document_url")]

    with httpx.Client(follow_redirects=True) as client:
        for i, product in enumerate(targets, 1):
            url = product["document_url"]
            local = PDF_CACHE / local_filename(url)
            product["local_path"] = str(local.relative_to(REPO_ROOT))

            if local.exists() and local.stat().st_size > 0:
                stats["cached"] += 1
                continue

            ok = False
            for attempt in range(MAX_RETRIES):
                try:
                    response = client.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                    response.raise_for_status()
                    local.write_bytes(response.content)
                    ok = True
                    break
                except httpx.HTTPError:
                    time.sleep(2 ** attempt)

            if ok:
                stats["downloaded"] += 1
            else:
                stats["failed"] += 1
                product["local_path"] = None

            if verbose and i % 50 == 0:
                print(f"  {i}/{len(targets)}  downloaded={stats['downloaded']} "
                      f"cached={stats['cached']} failed={stats['failed']}", flush=True)
            time.sleep(PAUSE_SECONDS)

    stats["no_url"] = sum(1 for p in products if not p.get("document_url"))
    return stats


def local_filename(url: str) -> str:
    """Stable local name for a document URL."""
    name = url.rstrip("/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def main() -> int:
    print(f"Ingesting {SOURCE_ID} from {LISTING_URL}")
    html = fetch_listing()
    products = parse_listing(html)
    print(f"  listing rows parsed: {len(products)}")

    with_docs = sum(1 for p in products if p.get("document_url"))
    print(f"  rows with a document link: {with_docs}")

    if not products:
        print("ERROR: no products parsed; refusing to write an empty snapshot",
              file=sys.stderr)
        return 1

    print("\nCaching documents (resumable)...")
    stats = cache_documents(products)
    print(f"\n  downloaded {stats['downloaded']}  already cached {stats['cached']}  "
          f"failed {stats['failed']}  no url {stats['no_url']}")

    note = (
        f"listing rows={len(products)}; with_document_url={with_docs}; "
        f"downloaded={stats['downloaded']}; cached={stats['cached']}; "
        f"failed={stats['failed']}. "
        "COVERAGE LIMIT: TMDA publishes SmPCs/TPARs for selected products only; "
        "this is a published subset, not the complete Tanzanian register."
    )
    snap = write_snapshot(SOURCE_ID, LISTING_URL, products, note=note)
    print(f"\nsnapshot   {snap.path}")
    print(f"records    {snap.record_count}")
    print(f"sha256     {snap.sha256}")
    print(f"retrieved  {snap.retrieved_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
