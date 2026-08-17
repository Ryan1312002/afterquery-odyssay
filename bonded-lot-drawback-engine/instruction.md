# Bondwright

`/app` is the repo for **Bondwright**, the lot ledger North River Bonded has been running off a spreadsheet plus a half-finished intern sprint. Newark wants this to be the system of record for type-21 warehouse entries, manufacturing-in-bond, and unused-merchandise drawback. Legal will replay the journal; ops will FIFO withdrawals against the five-year clock; finance will ask “if we withdrew everything for consumption tonight, what do we owe CBP?”

The warehouse rules memo and the Python API note are already in `docs/`. Those two documents are the contract. The code in `bonded/` is a skeleton from the intern sprint — it has the right module layout and a CLI entry point, but nobody has signed off on the math, the clock, or drawback. Treat the memo as source of truth when the code disagrees.

What we need in the tree when you’re done:

- An importable package `bonded` (see `docs/API.md`) that applies a journal of events and answers inventory, duty-liability, drawback-available, aging, and claims questions as-of a date.
- The `bonded` console script working the way the API note describes (`python -m bonded` is fine too).
- `pytest tests/public` green. That’s the smoke suite the warehouse leads will run. It is not the acceptance suite.

Stay inside this repo. No new runtime dependencies — stdlib only. Don’t invent extra CBP forms or ACE filings; we only need the lot ledger, manufacturing-in-bond yields, and unused / substitution drawback. If a case isn’t in the memo, fail closed with the errors named in the API note rather than guessing.

Worked journal from last March is in `examples/newark_week.json` with the HTS table in `data/hts_duty.json`. Replay that and sanity-check the reports before you call it done.
