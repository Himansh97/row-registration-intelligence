# Architecture

The system reads public national medicines registers and turns them into dated,
evidence backed signals. Everything is organised around one constraint: any
number it produces has to be traceable back to bytes that were retrieved on a
known date from a named source.

## Layers

```
  config/sources.yaml
        |            Declarative source registry. Nothing is fetched that is
        |            not declared here.
        v
  src/rri/sources/   One adapter per register. Each implements fetch, map to
        |            ProductRecord, and declare its own coverage limits.
        |            Adding a market is one class, not a new codebase.
        v
  provenance.py      Raw records written to a hashed, dated snapshot and
        |            appended to a manifest. Nothing downstream reads a live
        |            URL. Everything reads a snapshot.
        v
  normalize.py       Canonical forms for ingredient, strength, dosage form,
  products.py        route and company. Register placeholders such as "NA" and
        |            "see Composition" collapse to missing.
        v
  match.py           Entity resolution. Is this the same company? Is this the
        |            same product? Every decision carries a confidence and the
        |            fields it matched on.
        v
  triggers.py        Dated signals from one snapshot: expired, renewal due,
  diff.py            recently granted, first entry. Diff adds what moved
        |            between two snapshots.
        v
  whitespace.py      Cross market analysis. Held in one market, not found in
  report.py          another, with the registration proving the holding.
        v
  language.py        Output guard. Runs against generated text before it is
                     returned, not only in tests.
```

## The adapter contract

`sources/base.py` defines what a register adapter must do. Three methods and
nothing else:

| Method | Responsibility |
|---|---|
| `fetch()` | Retrieve raw records. Must never return a silently truncated set. |
| `to_records()` | Map raw source rows onto `ProductRecord`. |
| `coverage()` | State what the source contains and what it omits. |

Everything downstream works on `ProductRecord` and never knows which register a
row came from. The base class provides snapshotting, loading and refresh.

The contract also carries a `verify` field for the TLS trust store. ANVISA
serves an incomplete certificate chain, so it needs a bundle containing the
missing intermediate. Verification is never disabled. A pipeline whose claim is
traceability cannot make unauthenticated fetches.

## The provenance chain

```
  live source
      |  fetch
      v
  raw records  ------> sha256 + retrieval date -----> manifest.jsonl
      |                                                    |
      |  map                                               | verified by
      v                                                    v
  ProductRecord.source_ref  points back at snapshot   tests/test_citations.py
      |
      |  derive
      v
  trigger / whitespace cell / market row
      carries evidence text naming the source and the retrieval date
```

For documents rather than data, extraction goes further. Each extracted value
stores the page it came from and the verbatim quote supporting it. The citation
verifier re opens every source PDF and asserts the quote appears on the page it
claims. A value that cannot produce a supporting quote is not recorded at low
confidence. It is not recorded.

## Two release gates

Both are pytest suites. Both block release rather than filing a warning.

**Citation verifier.** Every stored quote appears on its cited page. Every value
is contained in its own quote. Snapshot hashes match the bytes on disk. A
citation that does not resolve is indistinguishable from a fabricated one.

**Output linter.** No generated artifact asserts a regulatory status or a
judgement the sources cannot support. Eight phrasings are blocked. The supportable
form is "not found in TMDA source, retrieved 2026 08 30" and "registration
expired 2026 03 14". The blocked form is "not registered in Tanzania" and "failed
to renew". Companies discontinue products deliberately. The register records the
date, never the reason.

## Design decisions that are easy to get wrong

**No vector database.** The corpus is small enough that structured extraction
into DuckDB stays auditable line by line. A retrieval stack would add embedding
drift to explain and buy nothing.

**Company matching is token based, never substring.** Searching for "sun pharma"
inside applicant names matches "Anisun Pharmaceutical Company Limited", which
invents a corporate relationship that does not exist. Matching requires the first
distinctive token to be equal, then a similarity threshold on top. First token
equality alone is not enough either, because "Micro Labs" and "Micro Nova
Pharmaceuticals" share one.

**Diffing keys on the register's own row id.** Registration numbers look like
identifiers and are not. In NAFDAC, 263 of them are shared across 538 records,
and one number covers two unrelated products. Keying on them hides real changes
and manufactures false ones. Row position is never used, because a register that
inserts one row shifts every index after it.

**Disappearance is suppressed when a snapshot shrinks implausibly.** If a fetch
partially fails, thousands of records vanish and a naive diff reads that as mass
deregistration. Above a twenty percent shrink, removals are not reported at all
and the output says why.

**Only active registrations count as coverage.** A lapsed registration is not
market access.

**Coverage windows are per source.** A source whose latest record is 2023 cannot
evidence anything about a product first registered elsewhere in 2025. Pairs that
cross that boundary are set aside as out of window, counted and reported rather
than dropped.

## Data flow for a single market

Nigeria is the simplest case and shows the whole path.

1. `ingest_nafdac.py` pages a server side DataTables endpoint. Page size 500,
   because 1000 makes the server drop the connection. Retries with backoff,
   because a short read becomes phantom whitespace downstream.
2. Records are snapshotted with a hash and a date.
3. `from_nafdac` maps each row onto `ProductRecord`, carrying the register's own
   `product_id` into extras so the record stays identifiable across snapshots.
4. `triggers.detect` reads expiry, status and approval dates and emits dated
   signals, each with evidence naming the source and retrieval date.
5. `diff.diff` compares the two most recent snapshots and reports what moved.

Tanzania is the hardest case. Its listing has no company field at all, so the
marketing authorisation holder is extracted from 1,027 linked SmPC and assessment
report PDFs, which disagree on heading wording, section numbering, registration
number format and layout.
