# Register Signal

Reads public national medicines registers and produces **dated, named reasons to
contact a pharmaceutical company**, registrations that have expired, renewals
falling due, and companies actively filing.

Built on public regulatory data only. Every figure traces to a hashed, dated
snapshot of the bytes it came from.

---

## Why this exists

Regulatory-services business development has no trigger events. Software sales
has renewal dates. Recruiting has job postings. This has conferences and
relationships. You call to say "we do regulatory services", which is what
everyone says, and there is no reason to call *today*.

Public registers are full of triggers nobody reads.

| Trigger | Meaning |
|---|---|
| **Expired** | Market access already lost. Recoverable only by re-filing. |
| **Renewal due** | A dated obligation. The filing happens or the product leaves the market. |
| **Recently granted** | The company is actively spending on regulatory work. |
| **First entry** | A company's first registration in this market, expanding. |

Ranked in that order, because a loss already taken outranks a future obligation,
which outranks a sign of momentum.

---

## Sources

| Country | Authority | Records | Triggers | Notes |
|---|---|---:|---:|---|
| 🇳🇬 Nigeria | NAFDAC Greenbook | 9,008 | 4,147 | Ingredient, strength, form, expiry, status |
| 🇧🇷 Brazil | ANVISA | 29,274 | 2,752 | Ingredient, expiry, status, CNPJ. Also publishes prices and review durations |
| 🇹🇿 Tanzania | TMDA | 848 | **0** | Neither expiry nor reliable dates, produces nothing rather than guesses |

A source that cannot support a claim does not make one. Tanzania returning zero
is the system working, not failing.

See [`docs/decisions.md`](docs/decisions.md) for every source assessed, including
the five rejected and why.

---

## The distinction everything rests on

A gap in a register has exactly one supportable reading:

> **not found in TMDA source, retrieved 2026-08-30**

and not:

> ~~not registered in Tanzania~~

And an expired registration is a fact about the register, never about the
company's conduct:

> **registration expired 2026-03-14; recorded inactive in NAFDAC, retrieved 2026-09-01**

and not:

> ~~failed to renew~~

Companies discontinue products deliberately. The register records the date, never
the reason. `src/rri/language.py` blocks eight phrasings, the generators run it
against their own output before returning, and the test suite runs it over every
generated artifact. **A report that overclaims raises instead of being written.**

---

## How it works

```
config/sources.yaml     every source declared; nothing fetched that is not here
        │
        ▼
  adapters ───────────► sources/base.py defines fetch, map, declare limits
        │               adding market N+1 is one class, not a new codebase
        ▼
  snapshot ───────────► raw bytes + sha256 + retrieval date
        │               nothing downstream reads a live URL
        ▼
  normalise ──────────► INN · strength · form · company
        │               placeholders ("NA", "see Composition") collapse to missing
        ▼
  triggers ───────────► expired · renewal due · granted · first entry
        │               every one carries a date and its evidence
        ▼
  diff ───────────────► what moved between two snapshots
        │               suppressed entirely if a snapshot shrank implausibly
        ▼
  output ─────────────► self-checks its own language before returning
```

### Design choices worth defending

**No vector database.** Structured extraction into DuckDB is auditable line by
line. A RAG stack would add embedding drift to explain and buy nothing.

**Company matching is token-based, never substring.** Searching for `"sun pharma"`
inside applicant names matches **"Ani*sun Pharma*ceutical Company Limited"**,
inventing a corporate relationship that does not exist.

**Diffing keys on the register's own row id, not the registration number.** In
NAFDAC, 263 registration numbers are shared across 538 records, and one number
covers two unrelated products.

**Only Active registrations count as coverage.** A lapsed registration is not
market access.

**TLS verification is never disabled.** ANVISA serves an incomplete certificate
chain; the fix was to supply the missing intermediate, not to stop checking. A
pipeline whose claim is traceability cannot make unauthenticated fetches.

---

## Release gates

Two suites block release rather than filing warnings.

| Gate | What it asserts |
|---|---|
| **Citation verifier** | Every extracted quote appears on the page it cites; every value is contained in its own quote; snapshot hashes match disk |
| **Output linter** | No generated artifact asserts a regulatory status or a judgement the sources cannot support |

A citation that does not resolve is indistinguishable from a fabricated one.

---

## Running it

### Run it locally

```bash
./.venv/bin/streamlit run app.py     # http://localhost:8501
```

Four tabs: the trigger feed, a discovery call scoper, company portfolios and
market structure. It calls the same Python the tests cover, so there is no
second implementation to keep in step, and it picks up the Claude extractor
automatically when ANTHROPIC_API_KEY is set.

### Build the corpus first

```bash
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt

PYTHONPATH=src ./.venv/bin/python -m rri.ingest_nafdac     # Nigeria
PYTHONPATH=src ./.venv/bin/python -m rri.sources.anvisa    # Brazil
PYTHONPATH=src ./.venv/bin/python -m rri.ingest_tmda       # Tanzania listing
PYTHONPATH=src ./.venv/bin/python -m rri.extract_tmda      # holders from SmPCs

PYTHONPATH=src ./.venv/bin/python -m rri.watch             # what moved since last time
PYTHONPATH=src ./.venv/bin/python -m rri.export_web        # build the product payload

PYTHONPATH=src ./.venv/bin/python -m pytest tests/ -q      # both release gates
```

The Tanzanian document cache (~325 MB) is not committed. It is re-fetchable from
the URLs recorded in `data/snapshots/`, and the manifest carries the hash of what
was retrieved, so provenance survives without shipping the bytes.

---

## History is the only moat

Scrapers are copyable in an afternoon. A year of dated snapshots is not.

Single-snapshot triggers work on day one. No cold start. Change-based signals
(status flips, removals, renewals observed) switch on once a second retrieval
exists and get better indefinitely. First real comparison, two days apart:
**36 registrations removed, reconciling exactly with the record-count shortfall.**

---

## Limitations

Stated here rather than discovered by a reader.

- **Coverage is permanently patchy.** Some regulators publish nothing usable.
  Uganda has licence holders but no active ingredient; Ghana loads over ajax;
  Zambia is a single-page app; Kenya's domain does not resolve.
- **Registered ≠ marketed.** A live registration is permission to sell, not
  evidence of sales. Brazil's CMED prices are regulated *ceilings*, not realised
  prices.
- **Brazil does not join cross-market comparison.** Its ingredient names are
  Portuguese; translation to English INN is a separate verified step.
- **Trigger volume is not conversion.** Nobody knows the trigger→meeting rate,
  and the product does not imply one.
- **Corporate groups are inferred from names**, not ownership records.
- **Not regulatory advice.** Decision support only.
