"""Export the whole analysis as one JSON payload for the browser.

Everything is computed here, in Python, using the same matcher the tests cover.
The browser only renders. Re-implementing entity resolution in JavaScript would
mean two matchers that could disagree, and the one people looked at would be the
one nobody tested.

Two tiers come out of this:

  dual-market groups   present in both registers -> full whitespace analysis
  single-market groups present in one -> holdings only, and an explicit refusal
                       to compute whitespace, because absence from a small
                       source is not evidence of anything

Run:  python -m rri.export_web
"""

from __future__ import annotations

import json
from collections import defaultdict

from rri.analyze import load_nafdac, load_tmda
from rri.match import group_companies
from rri.provenance import REPO_ROOT
from rri.sources.anvisa import AnvisaAdapter
from rri.watch import AUTHORITY, records_from, snapshots_by_source
from rri.diff import diff as diff_snapshots
from rri.triggers import by_account, detect, summarise
from rri.whitespace import build_portfolio

OUT = REPO_ROOT / "data" / "web" / "payload.json"


# A registration granted within this window counts as recent movement.
RECENT_CUTOFF = "2024-08-31"


def build_market_structure(records, coverage) -> list[dict]:
    """Competitive structure per molecule, per market.

    Registration counts are a proxy for competitive intensity, not for market
    share. The registers record who is *allowed* to sell, never who does. In
    African markets, where commercial sales data is thin and expensive, that
    proxy is often the only structural signal available. It is labelled as a
    proxy everywhere it appears.
    """
    from collections import defaultdict

    holders: dict = defaultdict(set)
    grants: dict = defaultdict(list)
    forms: dict = defaultdict(set)

    for r in records:
        if not r.inn or not r.is_active or not r.is_medicine:
            continue
        key = (r.country, r.inn, r.form)
        if r.company_raw:
            holders[key].add(r.company_raw)
        if r.approval_date:
            grants[key].append(str(r.approval_date)[:10])
        forms[(r.country, r.inn)].add(r.form)

    by_country = {c.country: c for c in coverage}
    out = []
    for key, companies in holders.items():
        country, inn, form = key
        if not companies:
            continue
        dates = sorted(grants.get(key, []))
        recent = sum(1 for d in dates if d >= RECENT_CUTOFF)
        n = len(companies)
        out.append({
            "country": country,
            "authority": by_country[country].authority if country in by_country else country,
            "inn": list(inn),
            "label": " + ".join(w.title() for w in inn),
            "form": form,
            "holders": n,
            "holder_names": sorted(companies)[:12],
            "registrations": len(dates),
            "recent_registrations": recent,
            "first_grant": dates[0] if dates else None,
            "latest_grant": dates[-1] if dates else None,
            "structure": ("sole holder" if n == 1 else "two holders" if n == 2
                          else "contested" if n <= 5 else "crowded" if n <= 10
                          else "commodity"),
        })

    out.sort(key=lambda m: (-m["holders"], m["label"]))
    return out


def build_feed(sources) -> tuple[list[dict], dict, dict]:
    """Trigger feed: accounts ranked worst-first, with their triggers.

    Whitespace needs two markets to say anything. Triggers do not. They are
    within-market. So every source contributes independently, and a source
    that lacks the necessary dates contributes nothing rather than guesses.
    """
    all_triggers = []
    per_source = {}
    for records, cov in sources:
        found = detect(records, cov)
        all_triggers += found
        per_source[cov.country] = {
            "authority": cov.authority,
            "retrieved": cov.retrieved_date,
            "records": len(records),
            "triggers": len(found),
            "by_kind": summarise(found),
        }

    accounts = []
    for sig in by_account(all_triggers):
        accounts.append({
            "company": sig.company,
            "country": sig.country,
            "authority": sig.authority,
            "counts": sig.counts,
            "top_kind": sig.top_kind,
            "next_date": sig.earliest_date,
            "total": len(sig.triggers),
            "triggers": [
                {"kind": t.kind, "label": t.label, "product": t.product,
                 "reg": t.registration_number, "date": t.date,
                 "evidence": t.evidence}
                for t in sorted(sig.triggers, key=lambda x: (x.severity, x.date))
            ],
        })

    totals = summarise(all_triggers)
    totals["accounts"] = len(accounts)
    totals["total"] = len(all_triggers)
    return accounts, totals, per_source


def build_changes() -> dict:
    """What moved between the two most recent snapshots of each source.

    Empty until a source has been retrieved twice. That is the honest state on
    day one, not a gap to paper over.
    """
    out = {"sources": [], "total": 0}
    grouped = snapshots_by_source()

    for sid, snaps in sorted(grouped.items()):
        if sid not in AUTHORITY:
            continue
        authority = AUTHORITY[sid]
        if len(snaps) < 2:
            out["sources"].append({
                "source_id": sid, "authority": authority, "has_history": False,
                "snapshots": len(snaps), "changes": [], "counts": {},
                "note": "Only one snapshot so far. Nothing to compare against yet.",
            })
            continue

        before, after = snaps[-2], snaps[-1]
        result = diff_snapshots(records_from(before), records_from(after),
                               before.retrieved_date, after.retrieved_date, authority)
        out["sources"].append({
            "source_id": sid, "authority": authority, "has_history": True,
            "snapshots": len(snaps),
            "from": before.retrieved_date, "to": after.retrieved_date,
            "from_count": before.record_count, "to_count": after.record_count,
            "compared": result.compared,
            "suppressed": not result.disappearance_reported,
            "note": result.note,
            "counts": result.by_kind(),
            "changes": [
                {"kind": c.kind, "company": c.company, "product": c.product,
                 "before": c.before, "after": c.after, "evidence": c.evidence}
                for c in result.changes[:300]
            ],
        })
        out["total"] += len(result.changes)

    return out


def main() -> int:
    ng, ng_cov = load_nafdac()
    tz, tz_cov = load_tmda()
    if not ng_cov or not tz_cov:
        print("ERROR: Nigeria and Tanzania sources must be ingested first")
        return 1

    br_loaded = AnvisaAdapter().load()
    br, br_cov = br_loaded if br_loaded else ([], None)

    records = [r for r in ng if r.is_active and r.is_medicine] + list(tz)
    coverage = [ng_cov, tz_cov]

    # Triggers run per-source across everything available, including Brazil,
    # which does not join the whitespace comparison because its ingredient
    # names are Portuguese and translation is a separate verified step.
    feed_sources = [(ng, ng_cov), (tz, tz_cov)]
    if br_cov:
        feed_sources.append((br, br_cov))
    feed_accounts, feed_totals, feed_sources_meta = build_feed(feed_sources)

    names = [r.company_raw for r in records if r.company_raw]
    groups = group_companies(names)
    member_to_rep = {m: rep for rep, members in groups.items() for m in members}

    # Which registers each corporate group appears in.
    presence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in records:
        if record.company_raw:
            presence[member_to_rep[record.company_raw]][record.country] += 1

    index, detail = [], {}
    for rep, counts in presence.items():
        dual = len(counts) > 1
        entry = {
            "id": rep,
            "name": rep,
            "aliases": sorted(groups[rep]),
            "ng": counts.get("NG", 0),
            "tz": counts.get("TZ", 0),
            "dual": dual,
        }
        index.append(entry)

        if not dual:
            continue

        p = build_portfolio(records, rep, coverage)
        detail[rep] = {
            "entities": {k: sorted(v) for k, v in p.entities.items()},
            "matrix": [
                {"label": label, "ng": pres.get("NG", False), "tz": pres.get("TZ", False)}
                for label, pres in p.matrix()
            ],
            "whitespace": [
                {
                    "product": c.product.label,
                    "target": c.country,
                    "authority": c.authority,
                    "held": list(c.present_in),
                    "reg": c.source_registration,
                    "evidence": c.evidence,
                    "competitors": c.context.competitors if c.context else None,
                    "reading": c.context.reading if c.context else None,
                    "examples": list(c.context.examples) if c.context else [],
                }
                for c in p.whitespace
            ],
            "out_of_window": [
                {"product": o.product.label, "target": o.country,
                 "since": o.held_since, "cutoff": o.coverage_end_year}
                for o in p.out_of_window
            ],
            "unmatchable": [
                f"{r.country} · {r.product_name or '(no name)'} · "
                f"{r.registration_number or 'no reg. number'}"
                for r in p.unmatchable
            ],
        }

    index.sort(key=lambda e: (not e["dual"], -(e["ng"] + e["tz"]), e["name"]))

    markets = build_market_structure(records, coverage)

    payload = {
        "coverage": [
            {"country": c.country, "authority": c.authority,
             "records": c.record_count, "retrieved": c.retrieved_date,
             "limitation": c.limitation, "cutoff": c.coverage_end_year}
            for c in coverage
        ],
        "totals": {
            "groups": len(index),
            "dual": sum(1 for e in index if e["dual"]),
            "records": len(records),
            "whitespace": sum(len(d["whitespace"]) for d in detail.values()),
            "out_of_window": sum(len(d["out_of_window"]) for d in detail.values()),
        },
        "index": index,
        "detail": detail,
        "markets": markets,
        "feed": {
            "accounts": feed_accounts,
            "totals": feed_totals,
            "sources": feed_sources_meta,
        },
        "changes": build_changes(),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    size_kb = OUT.stat().st_size / 1024
    t = payload["totals"]
    print(f"groups         {t['groups']:,}  ({t['dual']} in both registers)")
    print(f"records        {t['records']:,}")
    print(f"whitespace     {t['whitespace']}")
    print(f"out-of-window  {t['out_of_window']}")
    print(f"markets        {len(payload['markets']):,} molecule-market rows")
    ch = payload["changes"]
    print(f"triggers       {feed_totals['total']:,} across {feed_totals['accounts']:,} accounts")
    print(f"changes        {ch['total']:,} across "
          f"{sum(1 for s in ch['sources'] if s['has_history'])} source(s) with history")
    for country, meta in sorted(feed_sources_meta.items()):
        print(f"   {country}  {meta['authority']:<18} {meta['triggers']:>6,} triggers")
    print(f"payload        {OUT.relative_to(REPO_ROOT)}  ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
