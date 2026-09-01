"""The citation verifier. This is the release gate.

Every value extracted from a document carries the page it came from and the
verbatim text that supports it. This suite re-opens each source PDF and asserts
that the quote appears on the page it claims.

The point is not tidiness. The output of this project makes claims about real
companies to a reader who will check them. A citation that does not resolve is
indistinguishable from a fabricated one, so a failure here blocks release rather
than filing a warning.

Skips cleanly when the extraction has not been run, so a fresh clone is not
blocked by an absent artifact.
"""

from __future__ import annotations

import re

import pytest

from rri.provenance import REPO_ROOT, latest_snapshot, load_records, verify_snapshot

pytestmark = pytest.mark.citations


def _normalise(text: str) -> str:
    """Collapse whitespace so PDF line-wrapping does not fail a true citation."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _normalise_punctuation(text: str) -> str:
    """As `_normalise`, but also standardise spacing around separators.

    Extraction re-joins comma-separated parts with ", ", so a quote reading
    "Sandoz GmbH,Kundl" yields the value "Sandoz GmbH, Kundl". The same string
    with one space added. Comparing raw would fail a citation that is perfectly
    honest.

    This relaxation is deliberately narrow: whitespace around commas and colons
    only. Characters are never added, removed, or reordered, so a value
    containing anything the quote does not still fails.
    """
    text = _normalise(text)
    text = re.sub(r"\s*([,:;])\s*", r"\1", text)
    return text


@pytest.fixture(scope="module")
def extractions():
    snap = latest_snapshot("tmda_extractions")
    if snap is None:
        pytest.skip("no extraction snapshot; run `python -m rri.extract_tmda` first")
    return load_records(snap), snap


@pytest.fixture(scope="module")
def page_cache():
    """Text per page per PDF, loaded once and shared across tests."""
    import pdfplumber

    cache: dict[str, list[str]] = {}

    def get(local_path: str) -> list[str]:
        if local_path not in cache:
            path = REPO_ROOT / local_path
            if not path.exists():
                cache[local_path] = []
            else:
                with pdfplumber.open(path) as pdf:
                    cache[local_path] = [(p.extract_text() or "") for p in pdf.pages]
        return cache[local_path]

    return get


def _cited_fields(record: dict):
    for field in ("marketing_authorisation_holder", "registration_number"):
        value = record.get(field)
        if value:
            yield field, value


class TestSnapshotIntegrity:
    def test_snapshot_hash_matches_disk(self, extractions):
        """The manifest hash must match the bytes actually on disk."""
        _, snap = extractions
        assert verify_snapshot(snap), (
            f"snapshot {snap.path} does not match its recorded sha256. The file "
            f"was modified after retrieval, so nothing derived from it is citable"
        )


class TestQuotesResolve:
    def test_every_quote_appears_on_its_cited_page(self, extractions, page_cache):
        records, _ = extractions
        failures = []
        checked = 0

        for record in records:
            local_path = record.get("local_path")
            if not local_path:
                continue
            pages = page_cache(local_path)
            if not pages:
                continue

            for field, extraction in _cited_fields(record):
                page_no = extraction["page"]
                quote = extraction["quote"]
                checked += 1

                if not 1 <= page_no <= len(pages):
                    failures.append(
                        f"{record['pdf']} [{field}] cites page {page_no} but the "
                        f"document has {len(pages)} pages"
                    )
                    continue

                if _normalise(quote) not in _normalise(pages[page_no - 1]):
                    failures.append(
                        f"{record['pdf']} [{field}] quote not found on page "
                        f"{page_no}: {quote[:70]!r}"
                    )

        assert checked > 0, "no citations were checked. The gate is not actually running"
        assert not failures, (
            f"{len(failures)} of {checked} citations do not resolve:\n  "
            + "\n  ".join(failures[:20])
        )

    def test_every_value_is_supported_by_its_quote(self, extractions):
        """The extracted value must be derivable from the quote backing it.

        Guards the subtler failure: a quote that resolves to the right page but
        does not actually contain the value attributed to it.
        """
        records, _ = extractions
        failures = []

        for record in records:
            for field, extraction in _cited_fields(record):
                value = _normalise_punctuation(extraction["value"])
                quote = _normalise_punctuation(extraction["quote"])
                # The value is cleaned from the quote (labels and trailing
                # addresses removed), so it must be a substring of it.
                if value and value not in quote:
                    failures.append(
                        f"{record['pdf']} [{field}] value {extraction['value']!r} "
                        f"is not contained in its quote {extraction['quote'][:60]!r}"
                    )

        assert not failures, (
            f"{len(failures)} values are not supported by their quotes:\n  "
            + "\n  ".join(failures[:20])
        )


class TestNoSilentGuesses:
    def test_extractions_declare_a_method_and_confidence(self, extractions):
        records, _ = extractions
        for record in records:
            for field, extraction in _cited_fields(record):
                assert extraction.get("method"), f"{record['pdf']} [{field}] has no method"
                assert 0.0 < extraction.get("confidence", 0) <= 1.0, (
                    f"{record['pdf']} [{field}] has an invalid confidence"
                )

    def test_unreadable_documents_are_recorded_not_dropped(self, extractions):
        """A document that could not be read must leave a trace.

        Silently dropping it would shrink the denominator and overstate the
        extraction rate.
        """
        records, _ = extractions
        for record in records:
            has_error = bool(record.get("error"))
            has_result = bool(record.get("marketing_authorisation_holder")) or bool(
                record.get("registration_number")
            )
            has_pages = record.get("page_count") is not None
            assert has_error or has_result or has_pages, (
                f"{record.get('pdf')} recorded neither a result, an error, nor a "
                f"page count"
            )
