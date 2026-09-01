"""Extract marketing authorisation holder and registration number from TMDA SmPCs.

The TMDA listing has no company field. The marketing authorisation holder (without which no company-level analysis is possible) exists only inside the
linked SmPC and assessment-report PDFs.

Every value produced here carries the page it came from and the verbatim text
that supports it. `tests/test_citations.py` re-opens each PDF and asserts the
quote appears on the cited page. An extraction that cannot produce a
supporting quote is not recorded as a low-confidence guess; it is not recorded.

The documents are heterogeneous in every dimension that matters:

    heading      MARKETING AUTHORISATION HOLDER / Marketing authorization holder
    section      7. / 7.1 / 8.
    reg format   TAN 21 HM 0143 / TZ 17 H 0290
    domicile     foreign (Turkey) and local (Tanzania)

Run:  python -m rri.extract_tmda
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber

from rri.provenance import REPO_ROOT, latest_snapshot, load_records, write_snapshot

SOURCE_ID = "tmda_extractions"
PDF_CACHE = REPO_ROOT / "data" / "raw" / "tmda_pdfs"

# Both spellings, with or without a section number, as a heading of its own.
#
# The trailing group is deliberately permissive - observed headings include
# "... Holder and Manufacturing Site Addresses:", "... holder and manufacturer
# address", and a bare "... holder". What keeps this from matching
# pharmacovigilance boilerplate ("report ... to the marketing authorisation
# holder") is the ^ anchor plus the length cap: the heading must START the line
# and what follows it must be short enough not to be prose.
MAH_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)?\.?\s*)?"
    r"marketing\s+authori[sz]ation\s+holder\b"
    r"(?P<tail>[^.]{0,60})$",
    re.I,
)
# Same heading appearing inline with the value on the same line.
MAH_INLINE = re.compile(
    r"marketing\s+authori[sz]ation\s+holder"
    r"(?:\s+and\s+manufacturing\s+site)?\s*:\s*(?P<value>.+)",
    re.I,
)
# Start of the next numbered section - where the holder block ends.
NEXT_SECTION = re.compile(r"^\s*\d+(?:\.\d+)?\.?\s+[A-Z]")

# A running page number sitting between the heading and the value.
PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")

# A nested restatement of the same heading, e.g. "1. Name and Address of
# Marketing Authorization Holder" directly beneath the section heading.
MAH_SUBHEADING = re.compile(
    r"(?:name\s+and\s+address|address(?:es)?)\s+of\s+.*authori[sz]ation\s+holder"
    r"|^\s*\d*\.?\s*marketing\s+authori[sz]ation\s+holder\b",
    re.I,
)

# Bare field labels that are not company names. Anchored so that a real company
# whose name merely begins with one of these words is not discarded.
REJECT_VALUE = re.compile(
    r"^(?:address(?:es)?|name|country|telephone|tel|fax|e-?mail|postal|"
    r"manufacturer|manufacturing\s+site|holder|applicant|city|state)\s*:?\s*$",
    re.I,
)

REG_NUMBER = re.compile(
    r"\b(?:TAN|TZ)\s?\d{2}\s?[A-Z]{1,3}\s?\d{3,5}\b", re.I
)

# Words that follow the heading without being part of the value.
BOILERPLATE_TAIL = re.compile(
    r"^\s*(?:and\s+manufacturing\s+site(?:\s+address(?:es)?)?"
    r"|and\s+manufacturer(?:'?s)?(?:\s+address(?:es)?)?"
    r"|address(?:es)?|details?|information|name)?\s*[:\-]?\s*",
    re.I,
)

# Lines that are addresses or contact details rather than a company name.
#
# The Indian revenue-administration terms are here because they are how the
# addresses of Indian manufacturers are actually written, and without them
# "Vill: Nandpur, Teh: Baddi, Distt:Solan" reads as a company name.
ADDRESS_HINT = re.compile(
    r"\b(p\.?\s?o\.?\s?box|plot|street|road|str\.|avenue|phone|tel|fax|e-?mail|"
    r"www\.|@|postal|zip"
    r"|vill\.?|village|teh\.?|tehsil|distt\.?|dist\.?|district|taluka|tal\.?"
    r"|industrial\s+area|gidc|midc|sez|survey\s+no|gat\s+no|khasra"
    r"|estate|phase\s+[ivx\d]|sector\s+\d)\b",
    re.I,
)
# A company name almost always carries one of these.
COMPANY_HINT = re.compile(
    r"\b(ltd|limited|plc|inc|corp|company|co\.|gmbh|a\.?\s?ş|as|a/s|sa|srl|"
    r"s\.a\.|bv|nv|pvt|private|llc|bhd|sdn|pty|oy|ab|ag|kg|spa|aps|"
    r"pharma|pharmaceutical|laborator|laboratoires|labs|industr|health|"
    r"lifesciences|life\s+sciences|biotech|remedies|drugs|ilac|ila\u00e7|"
    r"sanayi|nordisk|servier)\b",
    re.I,
)


@dataclass(frozen=True)
class Extraction:
    """One extracted value, with the evidence that supports it."""

    value: str
    page: int  # 1-indexed, as a reader would count
    quote: str  # verbatim text from that page
    method: str
    confidence: float


def page_texts(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def _flatten(pages: list[str]) -> list[tuple[int, str]]:
    """All lines in reading order, each tagged with its 1-indexed page.

    The holder's name is sometimes on a different page from its heading, so the
    scan has to be able to cross page boundaries.
    """
    return [(page_no, line)
            for page_no, text in enumerate(pages, start=1)
            for line in text.splitlines()]


def extract_mah(pages: list[str]) -> Extraction | None:
    """Find the marketing authorisation holder.

    Strategy, in order of confidence:
      1. Heading on its own line -> holder is the first company-like line under it
      2. Heading inline with a colon -> holder is the remainder of that line

    Between the heading and the name, real documents interpose page numbers,
    running heads, and nested sub-headings ("1. Name and Address of Marketing
    Authorization Holder" directly under "2. Marketing Authorization Holder and
    Manufacturing Site Addresses"). Each is skipped rather than treated as the
    end of the block. Address and contact lines are skipped too: the holder's
    address follows its name in every layout observed, so taking the first
    non-empty line blindly yields a street.
    """
    lines = _flatten(pages)

    for i, (page_no, line) in enumerate(lines):
        heading = MAH_HEADING.match(line)
        if not heading:
            continue

        # Assessment reports lay this out as a table row with no colon:
        #     "Marketing Authorization Holder Beta Drugs Ltd"
        # The name is already on the heading line. Walking to the next line
        # instead lands on the holder's street address and reports it as the
        # company - which is how "Vill: Nandpur, Teh: Baddi, Distt:Solan"
        # became a marketing authorisation holder.
        tail = BOILERPLATE_TAIL.sub("", heading.group("tail") or "").strip(" \t:.-")
        if len(tail) >= 3 and not ADDRESS_HINT.search(tail):
            return Extraction(
                value=_tidy(tail),
                page=page_no,
                quote=line.strip(),
                method="rule:heading-inline",
                confidence=0.95 if COMPANY_HINT.search(tail) else 0.75,
            )

        for page_no, candidate in lines[i + 1: i + 14]:
            stripped = candidate.strip(" \t:.-")
            if not stripped:
                continue
            if PAGE_NUMBER.match(stripped):
                continue  # running page number between heading and value
            if MAH_SUBHEADING.search(stripped):
                continue  # nested restatement of the same heading
            if REJECT_VALUE.match(stripped):
                continue  # bare field label such as "ADDRESS" or "Country"
            if NEXT_SECTION.match(candidate):
                break  # a different section - the block ended
            if ADDRESS_HINT.search(stripped):
                continue
            if len(stripped) < 3:
                continue
            confidence = 0.95 if COMPANY_HINT.search(stripped) else 0.75
            return Extraction(
                value=_tidy(stripped),
                page=page_no,
                quote=candidate.strip(),
                method="rule:heading",
                confidence=confidence,
            )

    for page_no, line in lines:
        match = MAH_INLINE.search(line)
        if match:
            value = match.group("value").strip(" \t:.-")
            if (value and not ADDRESS_HINT.search(value)
                    and not REJECT_VALUE.match(value) and len(value) >= 3):
                return Extraction(
                    value=_tidy(value),
                    page=page_no,
                    quote=line.strip(),
                    method="rule:inline",
                    confidence=0.9 if COMPANY_HINT.search(value) else 0.7,
                )
    return None


def extract_registration_number(pages: list[str]) -> Extraction | None:
    """Find the Tanzanian registration number, e.g. TZ 17 H 0290."""
    for page_no, text in enumerate(pages, start=1):
        match = REG_NUMBER.search(text)
        if not match:
            continue
        # Quote the whole line so a reader can see the number in context.
        for line in text.splitlines():
            if match.group(0) in line:
                return Extraction(
                    value=re.sub(r"\s+", " ", match.group(0)).upper(),
                    page=page_no,
                    quote=line.strip(),
                    method="rule:pattern",
                    confidence=0.95,
                )
    return None


# Labels that precede the value on the same line: "Name : Cipla Limited".
VALUE_LABEL = re.compile(r"^\s*(?:name|holder|company|applicant)\s*:\s*", re.I)

# A comma-separated fragment that is part of a postal address rather than the
# company name: starts with a number, is a bare postcode, or names a street.
ADDRESS_FRAGMENT = re.compile(
    r"^\s*(?:"
    r"\d"                                   # 184 Industrial Estate
    r"|[A-Za-z]{1,2}[-/]\d"                  # A/202, T-184 building codes
    r"|.*\b(?:str(?:asse|aße|eet)?|road|rd\.?|avenue|ave\.?|lane|box|plot|"
    r"floor|block|area|district|dist\.?|zone|estate|hub|park|phase|sector)\b"
    r")",
    re.I,
)


def _tidy(value: str) -> str:
    """Reduce an extracted line to just the company name.

    Layouts run the address onto the same line as the holder
    ("Dafra Pharma GmbH, Mühlenberg 7, 4052 Basel,"), and some prefix the value
    with a field label ("Name : INDCHEMIE HEALTH SPECIALITIES PVT.LTD"). Both
    have to come off, or the company key carries a street address into matching.
    """
    value = VALUE_LABEL.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()

    # Keep leading comma-separated parts until one looks like an address.
    parts = [p.strip() for p in value.split(",")]
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if kept and ADDRESS_FRAGMENT.match(part):
            break
        kept.append(part)
    value = ", ".join(kept) if kept else value

    return value.strip().rstrip(",;.")


def extract_one(pdf_path: Path) -> dict:
    """Extract every field of interest from one document."""
    try:
        pages = page_texts(pdf_path)
    except Exception as exc:  # a corrupt or image-only PDF is a coverage gap
        return {"pdf": pdf_path.name, "error": f"{type(exc).__name__}: {exc}"}

    if not any(p.strip() for p in pages):
        # Scanned document with no text layer. Recorded as a gap rather than
        # silently returning nothing, so coverage stays honest.
        return {"pdf": pdf_path.name, "error": "no extractable text layer",
                "page_count": len(pages)}

    mah = extract_mah(pages)
    reg = extract_registration_number(pages)
    return {
        "pdf": pdf_path.name,
        "page_count": len(pages),
        "marketing_authorisation_holder": asdict(mah) if mah else None,
        "registration_number": asdict(reg) if reg else None,
    }


def main() -> int:
    listing = latest_snapshot("tmda_approved_products")
    if listing is None:
        print("ERROR: no TMDA listing snapshot found; run rri.ingest_tmda first",
              file=sys.stderr)
        return 1

    products = load_records(listing)
    by_pdf = {}
    for product in products:
        if product.get("local_path"):
            by_pdf[Path(product["local_path"]).name] = product

    pdfs = sorted(PDF_CACHE.glob("*.pdf"))
    print(f"Extracting from {len(pdfs)} cached documents "
          f"({len(products)} listing rows)")

    results = []
    stats = {"mah": 0, "reg": 0, "errors": 0}
    for i, pdf_path in enumerate(pdfs, 1):
        record = extract_one(pdf_path)
        product = by_pdf.get(pdf_path.name, {})
        record["product_name"] = product.get("product_name")
        record["generic_name"] = product.get("generic_name")
        record["document_url"] = product.get("document_url")
        record["local_path"] = str(pdf_path.relative_to(REPO_ROOT))

        if record.get("error"):
            stats["errors"] += 1
        else:
            stats["mah"] += bool(record.get("marketing_authorisation_holder"))
            stats["reg"] += bool(record.get("registration_number"))
        results.append(record)

        if i % 100 == 0:
            print(f"  {i}/{len(pdfs)}  mah={stats['mah']} reg={stats['reg']} "
                  f"errors={stats['errors']}", flush=True)

    total = len(results)
    print(f"\n  documents          {total}")
    if total:
        print(f"  MAH extracted      {stats['mah']} ({100*stats['mah']/total:.1f}%)")
        print(f"  reg no extracted   {stats['reg']} ({100*stats['reg']/total:.1f}%)")
        print(f"  unreadable         {stats['errors']}")

    note = (f"documents={total}; mah_extracted={stats['mah']}; "
            f"reg_extracted={stats['reg']}; unreadable={stats['errors']}")
    snap = write_snapshot(SOURCE_ID, listing.url, results, note=note)
    print(f"\nsnapshot   {snap.path}")
    print(f"sha256     {snap.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
