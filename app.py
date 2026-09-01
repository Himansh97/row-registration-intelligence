"""Register Signal, running locally.

Run:  ./.venv/bin/streamlit run app.py

This calls the same Python the tests cover. Nothing is precomputed and shipped
to a browser, so there is no second implementation to keep in step, and the
Claude extractor is used automatically when ANTHROPIC_API_KEY is set.
"""

from __future__ import annotations

import streamlit as st

from rri.analyze import load_nafdac, load_tmda
from rri.sales import gaps as gapmod
from rri.sales.extract import RuleExtractor, default_extractor
from rri.sales.ground import Corpus
from rri.sales.scope import build_scope
from rri.sources.anvisa import AnvisaAdapter
from rri.triggers import by_account, detect, summarise
from rri.whitespace import build_portfolio

st.set_page_config(page_title="Register Signal", page_icon="📡", layout="wide")

KIND_LABEL = {
    "lapsed": "Expired",
    "renewal_due": "Renewal due",
    "newly_granted": "Recently granted",
    "first_entry": "First entry",
}


@st.cache_resource(show_spinner="Loading registers...")
def load_everything():
    """Every register currently ingested, loaded once per session."""
    sources = []
    records = []
    coverage: dict[str, str] = {}
    retrieved: dict[str, str] = {}

    ng, ng_cov = load_nafdac()
    if ng_cov:
        sources.append((ng, ng_cov))
        records += ng
        coverage["NG"] = ng_cov.authority
        retrieved["NG"] = ng_cov.retrieved_date

    tz, tz_cov = load_tmda()
    if tz_cov:
        sources.append((tz, tz_cov))
        records += tz
        coverage["TZ"] = tz_cov.authority
        retrieved["TZ"] = tz_cov.retrieved_date

    loaded = AnvisaAdapter().load()
    if loaded:
        br, br_cov = loaded
        sources.append((br, br_cov))
        records += br
        coverage["BR"] = br_cov.authority
        retrieved["BR"] = br_cov.retrieved_date

    corpus = Corpus(records=records, coverage=coverage, retrieved=retrieved)
    return sources, corpus


@st.cache_data(show_spinner="Reading the registers...")
def load_triggers():
    sources, _ = load_everything()
    found = []
    per_source = {}
    for records, cov in sources:
        got = detect(records, cov)
        found += got
        per_source[cov.country] = {
            "authority": cov.authority,
            "records": len(records),
            "retrieved": cov.retrieved_date,
            "triggers": len(got),
        }
    return found, by_account(found), summarise(found), per_source


def sidebar(per_source):
    st.sidebar.title("Register Signal")
    st.sidebar.caption(
        "Dated, evidence backed reasons to contact a pharmaceutical company, "
        "read out of public medicines registers."
    )
    st.sidebar.divider()
    st.sidebar.subheader("Sources")
    for country, meta in sorted(per_source.items()):
        st.sidebar.write(
            f"**{meta['authority']}** ({country})  \n"
            f"{meta['records']:,} records, retrieved {meta['retrieved']}  \n"
            f"{meta['triggers']:,} triggers"
        )
    st.sidebar.divider()
    st.sidebar.caption(
        "A source that publishes neither expiry nor reliable dates produces no "
        "triggers rather than inferred ones. Decision support, not regulatory advice."
    )


def page_feed(accounts, totals):
    st.header("Trigger feed")
    st.caption(
        "Ranked worst first. An expired registration is market access already "
        "lost. A renewal due is a dated obligation. Recent grants are momentum."
    )

    c = st.columns(5)
    c[0].metric("Expired", f"{totals.get('lapsed', 0):,}")
    c[1].metric("Renewal due", f"{totals.get('renewal_due', 0):,}")
    c[2].metric("Recently granted", f"{totals.get('newly_granted', 0):,}")
    c[3].metric("First entry", f"{totals.get('first_entry', 0):,}")
    c[4].metric("Accounts", f"{len(accounts):,}")

    left, right = st.columns([1, 2], gap="large")

    with left:
        query = st.text_input("Find a company", placeholder="EMS, Emzor, Ache...")
        kinds = st.multiselect("Trigger type", list(KIND_LABEL),
                               format_func=lambda k: KIND_LABEL[k])
        shown = [
            a for a in accounts
            if (not query or query.lower() in a.company.lower())
            and (not kinds or any(a.counts.get(k) for k in kinds))
        ]
        st.caption(f"{len(shown):,} accounts")
        labels = [
            f"{a.company[:44]}  ({a.country})  "
            f"{' '.join(f'{v} {KIND_LABEL[k].lower()}' for k, v in sorted(a.counts.items()))}"
            for a in shown[:300]
        ]
        if not labels:
            st.info("No account matches that filter.")
            return
        picked = st.radio("Account", range(len(labels)),
                          format_func=lambda i: labels[i], label_visibility="collapsed")
        account = shown[picked]

    with right:
        st.subheader(account.company)
        st.caption(f"{account.authority}  ·  {account.country}  ·  "
                   f"next dated event {account.earliest_date}")
        counts = account.counts
        if counts.get("lapsed"):
            st.warning(
                f"{counts['lapsed']} registration(s) recorded as expired in the last "
                f"12 months. Market access for those products has lapsed and would "
                f"need re-filing to restore."
            )
        elif counts.get("renewal_due"):
            st.info(
                f"{counts['renewal_due']} registration(s) expiring within 12 months, "
                f"the earliest on {account.earliest_date}."
            )
        st.dataframe(
            [
                {"Trigger": KIND_LABEL.get(t.kind, t.kind), "Product": t.product,
                 "Registration": t.registration_number or "n/a", "Date": t.date,
                 "Evidence": t.evidence}
                for t in sorted(account.triggers, key=lambda x: (x.severity, x.date))
            ],
            width='stretch', hide_index=True,
        )
        st.caption(
            "A registration can lapse because a company chose to discontinue the "
            "product. The register records the expiry, not the reason, and neither "
            "does this."
        )


def page_scope(corpus):
    st.header("Scope a call")
    st.caption(
        "Every line that comes back points at words in the note. Nothing is "
        "inferred from what the client might have meant."
    )

    extractor = default_extractor(corpus.ingredients)
    using_model = type(extractor).__name__ == "ClaudeExtractor"
    st.caption(
        f"Extractor in use: **{'Claude' if using_model else 'rules'}**. "
        + ("" if using_model else
           "Set ANTHROPIC_API_KEY to use the model. Both are held to the same "
           "span check, so this affects recall and never whether an unsupported "
           "fact can get through.")
    )

    example = """Discovery call, Getz Pharma Nigeria Limited, 1 September.

They have 14 generic products registered in Nigeria and want to move into Brazil
and Tanzania. Mostly cardiovascular and respiratory. They named amlodipine,
atorvastatin and celecoxib specifically.

Asked whether we handle dossier preparation in CTD format. They think three of
their products may already be registered in Tanzania through a local partner but
were not certain.

Brazil filings need to be done by Q3 2027 because of a tender."""

    if st.button("Load an example call"):
        st.session_state["note"] = example
        st.session_state["company"] = "Getz Pharma"

    note = st.text_area("Call note", key="note", height=260,
                        placeholder="Paste discovery call notes, an email thread, or RFP text...")
    company = st.text_input("Client legal entity, if known", key="company",
                            placeholder="so existing registrations can be looked up")

    if not note.strip():
        return

    scope = build_scope(note, corpus, company.strip() or None, extractor=extractor)
    held = gapmod.already_held(scope.lines)
    counts = scope.counts()

    c = st.columns(4)
    c[0].metric("Facts read", counts["facts"])
    c[1].metric("Scope lines", counts["lines"])
    c[2].metric("Already held", len(held))
    c[3].metric("Open questions", counts["gaps"])

    if scope.can_be_quoted:
        st.success("No high impact questions open. Confirm the medium impact items "
                   "before quoting.")
    else:
        st.warning("Not ready to quote. High impact questions below are still open. "
                   "Answering them changes what the work is, not just what it costs.")

    for line in held:
        st.success(f"**Already held: {line.product.title()} in {line.market}.** "
                   f"{line.grounding.evidence}")
    if held:
        st.caption("Quoting to file these again, in front of someone who knows their "
                   "own portfolio, does not recover in that meeting.")

    st.subheader("Scope lines")
    if scope.lines:
        st.dataframe(
            [
                {"Product": l.product.title(), "Market": l.market,
                 "Service": l.service.replace("_", " "),
                 "Holders": ("client holds it" if l.grounding.already_held
                             else f"{l.grounding.competitors} others"),
                 "What the register shows": l.grounding.evidence}
                for l in scope.lines
            ],
            width='stretch', hide_index=True,
        )
    else:
        st.info("None. The note did not establish a product, a market and a service "
                "together, which is the minimum needed to describe a unit of work.")

    st.subheader("Ask before quoting")
    for impact in ("high", "medium", "low"):
        items = [g for g in scope.gaps if g.impact == impact]
        if not items:
            continue
        st.markdown(f"**{impact.title()} impact**")
        for gap in items:
            st.markdown(f"- **{gap.question}**  \n  {gap.why_it_matters}")

    if scope.unresolved:
        st.subheader("Not checked")
        st.caption("Mentioned in the call but outside what the register corpus can "
                   "confirm. These are limits on what was verified, not conclusions.")
        for item in scope.unresolved:
            st.markdown(f"- **{item.fact.value}** ({item.fact.kind}). {item.note}")

    with st.expander("What the client said, word for word"):
        st.dataframe(
            [{"Read as": f.kind.replace("_", " "), "Value": f.value,
              "Their words": f.span.text} for f in scope.facts],
            width='stretch', hide_index=True,
        )

    how_it_works(note)


def how_it_works(note: str) -> None:
    """The mechanism, demonstrated live on whatever note is in the box.

    Explaining that a model cannot fabricate through this is weaker than
    showing it, so this runs the real extraction path against deliberately
    invented proposals and prints what happens to them.
    """
    with st.expander("How this works, and why a model cannot invent a client"):
        st.markdown(
            "Whatever proposes a fact, a regular expression or a language model, "
            "has to hand back the words that support it. Those words are then "
            "located in the note. If they are not there, the fact is dropped.\n\n"
            "That single check is what makes a model safe here. It cannot invent "
            "a product the client never mentioned, because the quote backing the "
            "invention would not appear in the note."
        )

        class Planted:
            """Proposes two things that are not in the note, and one that is."""

            name = "planted"

            def __init__(self, source):
                first = next((w for w in source.split() if len(w) > 6), "the")
                self._p = [
                    {"kind": "product", "value": "rivaroxaban",
                     "quote": "they also mentioned rivaroxaban", "confidence": 0.99},
                    {"kind": "market", "value": "IN",
                     "quote": "expanding into India", "confidence": 0.95},
                    {"kind": "constraint", "value": first,
                     "quote": first, "confidence": 0.6},
                ]

            def propose(self, text):
                return self._p

        from rri.sales.extract import extract as run_extract

        kept, rejected = run_extract(note, Planted(note))
        st.markdown("**Fed three proposals against the note above:**")
        for r in rejected:
            st.error(f"Rejected. Claimed **{r.get('value')}**, quoting "
                     f"\u201c{r.get('quote')}\u201d. {r['reason']}.")
        for f in kept:
            st.success(f"Kept. Claimed **{f.value}**, quoting "
                       f"\u201c{f.span.text}\u201d, which is in the note.")
        st.caption(
            "Confidence 0.99 does not rescue an unsupported fact. Rejections are "
            "returned rather than discarded, because how often an extractor "
            "proposes things that are not in the source is a quality signal."
        )

        st.markdown("**What this will not do**")
        st.markdown(
            "- **No effort hours or day rates.** Registers record what is approved, "
            "never what it costs. A made up number is discredited by the first "
            "person who signs those contracts.\n"
            "- **No review times.** Approval dates say when a registration was "
            "granted, not how long review took. Without a submission date that "
            "duration is not observable, so none is offered.\n"
            "- **No invented scope.** A line is only produced for a product, market "
            "and service the client actually raised.\n"
            "- **No status claims.** Absence from a register is reported as what a "
            "search returned on a date, never as a statement about whether a "
            "product is registered."
        )


def page_company(corpus, sources):
    st.header("Company view")
    st.caption("Products a group holds in one market and that were not found in "
               "another, each with the registration proving the holding.")

    coverage = [cov for _, cov in sources]
    name = st.text_input("Corporate group", placeholder="Getz Pharma, Hetero Labs, Bayer...")
    if not name.strip():
        return

    portfolio = build_portfolio(corpus.records, name.strip(), coverage)
    if not portfolio.products and not portfolio.unmatchable:
        st.info(f"No records found for {name} in the sources searched.")
        return

    c = st.columns(4)
    c[0].metric("Entities resolved", sum(len(v) for v in portfolio.entities.values()))
    c[1].metric("Products", len(portfolio.products))
    c[2].metric("Unfiled pairs", len(portfolio.whitespace))
    c[3].metric("Out of window", len(portfolio.out_of_window))

    if portfolio.entities:
        st.caption("Registers record the local marketing entity rather than the "
                   "parent, so these were clustered into one group: "
                   + ", ".join(sorted(n for v in portfolio.entities.values() for n in v)))

    if portfolio.whitespace:
        st.dataframe(
            [
                {"Product": w.product.label, "Target": f"{w.country} ({w.authority})",
                 "Held in": ", ".join(w.present_in) or "n/a",
                 "Proof": w.source_registration or "n/a",
                 "Who else is there": w.context.reading if w.context else "not assessed",
                 "Status in target": w.evidence}
                for w in portfolio.whitespace
            ],
            width='stretch', hide_index=True,
        )
        st.caption('"Who else is there" is a floor, not a count. It only counts '
                   "holders within the source searched, so it can only understate.")

    if portfolio.out_of_window:
        st.warning(
            f"{len(portfolio.out_of_window)} pair(s) excluded as out of window. The "
            f"holding postdates the latest record in the target source, so that "
            f"source could not have contained the product whatever its true status."
        )


def page_market(corpus):
    st.header("Market view")
    st.caption(
        "How crowded a molecule already is in a market. Registration counts are "
        "a proxy for competitive intensity, not market share. The register "
        "records who is permitted to sell, never who does or at what volume."
    )

    molecule = st.text_input("Molecule", placeholder="amlodipine, artemether, ceftriaxone...")
    if not molecule.strip():
        return

    key = molecule.strip().lower()
    if key not in corpus.ingredients:
        st.info(f"{molecule} was not found in the register corpus under that name. "
                f"It may be a brand name, or outside the markets covered.")
        return

    rows = []
    for country in sorted(corpus.markets):
        holders = corpus.holders(country, key)
        names = sorted({r.company_raw for r in holders if r.company_raw})
        if not names:
            rows.append({"Market": f"{country} ({corpus.authority(country)})",
                         "Holders": 0, "Reading": "none found in this source",
                         "Examples": ""})
            continue
        n = len(names)
        reading = ("sole holder" if n == 1 else "two holders" if n == 2
                   else "contested" if n <= 5 else "crowded" if n <= 10 else "commodity")
        rows.append({"Market": f"{country} ({corpus.authority(country)})",
                     "Holders": n, "Reading": reading,
                     "Examples": ", ".join(names[:4])})

    st.dataframe(rows, width='stretch', hide_index=True)
    st.caption(
        "A count is a floor. It only counts holders within the source searched, "
        "so a small source reads as empty more often than a large one."
    )


def main() -> None:
    sources, corpus = load_everything()
    if not sources:
        st.error("No registers ingested yet. Run the ingest commands in the README first.")
        return

    _, accounts, totals, per_source = load_triggers()
    sidebar(per_source)

    feed, scope_tab, company, market = st.tabs(
        ["Trigger feed", "Scope a call", "Company view", "Market view"])
    with feed:
        page_feed(accounts, totals)
    with scope_tab:
        page_scope(corpus)
    with company:
        page_company(corpus, sources)
    with market:
        page_market(corpus)


main()
