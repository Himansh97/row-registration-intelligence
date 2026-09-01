# Decision log

Source-selection decisions, including the ones that did not work out. Recorded so
the reasoning is auditable and so nobody re-discovers a dead end.

---

## D1. NAFDAC Greenbook is the anchor register

**Date:** 2026-08-30 · **Status:** adopted

The Greenbook is served by a server-side DataTables endpoint returning JSON.
Retrieved the full register: **9,008 records**.

Field completeness on the retrieved snapshot:

| Field | Coverage |
|---|---|
| `ingredient_name` (INN) | 100% |
| `strength` | 100% |
| `form_name` | 100% |
| `route_name` | 100% |
| `applicant_name` | 100% |
| `approval_date` | 100% |
| `status` | 100% |
| `atc` | 94.4% |

ATC coverage at 94.4% makes it the strongest cross-register join key available.
`applicant_name` is what allows a company portfolio to be isolated.

**Correction to earlier research:** a Jan-2024 news article reported 6,432
products. The live endpoint reports 9,008. The live figure is used everywhere;
the article figure is not cited.

**Page size:** 500 is the largest page the endpoint serves reliably. 1,000 causes
the server to drop the connection. Probed, not assumed. Fetching retries with
exponential backoff, because a silently short read would erase real
registrations and manufacture whitespace that does not exist.

---

## D2. Status must be filtered

**Date:** 2026-08-30 · **Status:** adopted

The Greenbook snapshot splits **6,406 Active / 2,602 Inactive**. Whitespace has
to be computed against Active registrations only. Counting lapsed registrations
as live coverage would understate the opportunity; ignoring the field entirely
would misstate both directions.

---

## D3. Company matching cannot use substrings

**Date:** 2026-08-30 · **Status:** adopted

Registers record the **local marketing entity**, not the global parent,
"Ranbaxy Nigeria Limited", "Novartis Nigeria Limited", "Hetero Labs Nigeria
Limited". Corporate-group resolution is therefore required.

A naive substring search for `"sun pharma"` matched **"Ani*sun Pharma*ceutical
Company Limited"** and **"Daily *Sun Pharma*ceutical Company Ltd"**, neither of
which is Sun Pharmaceutical Industries. Substring matching silently invents
corporate relationships.

**Consequence:** matching is token-based with explicit corporate-suffix
stripping, and every match carries a confidence score and the fields it matched
on. Borderline matches go to a review queue rather than being resolved silently
in either direction.

---

## D4. Zambia (ZAMRA) deferred; Tanzania (TMDA) is register #2

**Date:** 2026-08-30 · **Status:** adopted (scope change from original plan)

**ZAMRA: deferred.** The public register is an Angular SPA at
`app.zamra.co.zm:42882/portal/#/public/registered-medicines`. The data sits
behind an undocumented REST API; the client bundle is 11.7 MB and does not
expose an obvious endpoint. Reverse-engineering it is unbounded work with no
guarantee of a stable result.

**TMDA: adopted.** `tmda.go.tz/pages/approved-product-information` lists
**1,080 products**, each linking to an SmPC or public assessment report PDF.
Sampling three confirmed the documents carry Marketing Authorisation Holder,
registration number, and date of first registration, and that they parse cleanly.

The documents are heterogeneous, which is why this needs grounded
extraction rather than a regex:

| Variation | Observed |
|---|---|
| Heading | `MARKETING AUTHORISATION HOLDER` / `Marketing authorization holder` |
| Section number | `7.` / `7.1` / `8.` |
| Registration format | `TAN 21 HM 0143` / `TZ 17 H 0290` |
| MAH domicile | Foreign (İlko İlaç, Turkey) and local (Shelys, Tanzania) |

**Why this is the better choice regardless:** paging a JSON API is not a
demonstration of capability. Turning 1,080 heterogeneous regulatory PDFs into a
structured, citable register is.

**Coverage limit that must travel with every Tanzania figure:** TMDA publishes
SmPCs/TPARs for *selected* products. 1,080 is a published subset, not the
complete Tanzanian register. Absence from it is not evidence of
non-registration.

---

## D5. Registers probed and rejected

**Date:** 2026-08-30

| Source | Result |
|---|---|
| AUDA-NEPAD AMRH | HTTP 403 to automated requests |
| Kenya PPB (`pharmacyboardkenya.org`) | No response (connection failed) |
| SAHPRA registered health products | Page reachable; no structured register file linked |
| Ghana FDA, Uganda NDA | Reachable; not yet assessed, candidates for extension |

---

## D6. Market context added: a gap alone is not a recommendation

**Date:** 2026-08-31 · **Status:** adopted

Whitespace said only that a product-market pair was unfiled. That is not enough to
act on: the same absence means opposite things depending on who else is present.

Every cell now carries a count of **other companies holding the same product in
the target market**, computed from the same registers and inheriting their
coverage limits. Ranking follows from it:

| Other holders | Reading | Rank |
|---|---|---|
| 1–3 | Proven pathway, room left | highest |
| 0 | Untapped, or a reason nobody is there | middle |
| 4+ | Crowded shelf | lowest |

Zero holders is deliberately **not** ranked best. An empty market is a question,
not an opportunity.

Blocking is by INN: two records cannot be the same product unless their
ingredients agree, so comparing within an ingredient bucket gives the same answer
as comparing everything.

---

## D7. Survey for a third register: all candidates rejected

**Date:** 2026-08-31 · **Status:** closed, unresolved

The two-market limit is real and was attacked directly. Five candidates assessed;
none usable without introducing inference the project refuses to make.

| Source | Result |
|---|---|
| **Uganda NDA** `nda.or.ug/drug-register/` | 2,930 rows, server-rendered, with licence holder, dosage form, and registration date. **Rejected: no active ingredient.** Drug names are brands: `SUPERPIME`, `AGOTRAX`, `VIFEX`, `EVECARE`. And only **9.3%** carry a strength. INN is the join key; supplying it would mean inferring ingredients from brand names via an external dictionary, which is exactly the guessing this project exists to avoid. |
| **Ghana FDA** `fdaghana.gov.gh/registry/` | Pages render no inline table; data is loaded by wpDataTables over ajax. Would need endpoint reverse-engineering. Not attempted. |
| **Kenya PPB** | Domain does not resolve. |
| **SAHPRA** (South Africa) | Page reachable; no structured register file published. |
| **ZAMRA** (Zambia) | Angular SPA over an undocumented API, 11.7 MB bundle. See D4. |

**Consequence, stated rather than hidden:** the analysis covers two markets. The
13-group overlap is a direct function of that. More markets would raise it; no
cheaply available market supplies the ingredient field required to join on.

**Not fixable from regulatory registers at all:** market sizing, pricing, and unit
volumes. Registers record what is approved, never what sells. Those need
commercial data of the IQVIA class and no amount of scraping substitutes for it.

---

## D8. Brazil (ANVISA) added; adapter contract introduced

**Date:** 2026-09-01 · **Status:** adopted

ANVISA publishes bulk open CSV, which makes Brazil the cheapest large market
available and the only source so far that also carries **regulated prices**
(CMED) and **real review-queue durations**. Two earlier conclusions in this
project were wrong and are corrected here: that registers never carry pricing,
and that actual timelines are unobtainable.

**43,445 raw records → 29,274 conventional → 9,843 active.**

Restricting to conventional categories is not cosmetic. Ingredient coverage
across all active records is 60%; within Similar / Genérico / Novo / Específico /
Biológico it is **99.9%**. The remainder are DINAMIZADO (homeopathic),
Fitoterápico (herbal) and BAIXO RISCO categories, which follow different routes
and are not comparable to a conventional medicines register.

Four traps, each of which silently corrupts the data rather than failing:

| Trap | Consequence if missed |
|---|---|
| Encoding is **ISO-8859-1** | Every accented company name mangles, and company names are a matching key |
| Expiry is **MMYYYY**, not a date | `062036` parses as the year 0620; the expiry histogram comes back empty |
| Holder carries the **CNPJ**. `05044984000126 - LEGRAND PHARMA…` | Discarded, entity resolution stays fuzzy when it could be exact |
| TLS chain is **incomplete** | See below |

**TLS.** ANVISA serves only its leaf certificate and omits the Sectigo
intermediate, so a strict client cannot build a path to a trusted root. curl and
browsers paper over this. The fix was to fetch the intermediate from the CA
Issuers URI in the leaf's own AIA extension and build a bundle of certifi's roots
plus that intermediate. **Verification stays on.** This pipeline's entire claim
is that its data traces to a source; an unauthenticated fetch cannot support that
claim, so disabling verification was never an option.

**Adapter contract.** `sources/base.py` defines what a register adapter must do:
fetch, map to `ProductRecord`, and declare its own coverage limits. ANVISA is the
first written against it. Nigeria and Tanzania migrate only once a second
implementation has proven the contract, refactoring working, citation-verified
code before that is risk without information.

**Bug found in my own work.** `coverage_end_year` was initially derived from
expiry dates and returned **2058**. That field exists to stop one source being
compared against another that sees further forward; expiry dates run decades
ahead. Corrected to approval dates: 2026.

---

## D9. Registration numbers are not unique, and diffing on them is wrong

**Date:** 2026-09-01 · **Status:** adopted

Snapshot diffing needs a stable identity per record. Registration number is the
obvious candidate and it is **wrong**.

In the NAFDAC register, **263 registration numbers are shared across 538
records**. Some of that is legitimate lifecycle. An expired row and its renewed
successor. Some is not:

```
A4-1205  →  "Dermovate Cream"    (Inactive)
A4-1205  →  "EBU 200 Tablets"    (Inactive)   ← unrelated product
A4-1205  →  "Dermovate Cream"    (Active)
```

Keyed on registration number, the first real diff produced **33 disappearances
and 1 expiry change** against a record-count shortfall of 36. The arithmetic did
not reconcile, which is what exposed it. The phantom `expiry_changed` came from
comparing two unrelated products that happened to share a number. A false
positive. While three genuine removals were hidden behind collisions.

**Fix:** prefer the register's own row identifier (`product_id` for NAFDAC),
falling back to registration number, then process number, then document URL.
Re-run: **9,008 compared, 36 disappeared, 0 unkeyed**, reconciling exactly with
9,008 − 8,972.

Row *position* is never used. A register that inserts one row shifts every index
after it, which would read as the entire file changing at once.

---

## D10. Disappearance is suppressed when a snapshot shrinks implausibly

**Date:** 2026-09-01 · **Status:** adopted

The worst failure available to this system is a partial fetch reported as mass
deregistration. If a retrieval silently drops half a register, a naive diff hands
a salesperson thousands of false "market access lost" signals about named
companies.

If a new snapshot holds **more than 20% fewer** keyed records than the previous
one, disappearances are not reported at all and the output says why. Registers do
prune, but not by a fifth between retrievals. Other change kinds, status flips,
new entries. Survive the guard, because those are still trustworthy.

First real comparison, 2026-08-30 → 2026-09-01: a 0.4% shrink, well inside the
threshold, and 36 removals reported.
