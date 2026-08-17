# Odyssey draft — Bonded lot ledger and duty-drawback engine

Paste these fields into the Odyssey draft form. `workingSlug` is `bonded-lot-drawback-engine`. The uploadable ZIP is `dist/bonded-lot-drawback-engine.zip`.

---

## title

Bonded lot ledger and duty-drawback engine

## workingSlug

bonded-lot-drawback-engine

## collectionFamily

Product clone

## taskFamily

feature_development

## verifierFamily

programmatic

---

## objective

Build Bondwright, the lot-ledger kernel for a US bonded warehouse (a working slice of a customs/3PL warehouse system). The agent starts from a real-looking Python package at `/app` whose intern skeleton is incomplete and wrong in several places. Done means:

1. `import bonded` exposes `Ledger`, `HtsTable`, and the named errors in `docs/API.md`.
2. `Ledger` is an event-sourced journal: `apply` / `apply_many` validate immediately; reports (`inventory`, `duty_liability`, `drawback_available`, `aging`, `claims`) reconstruct state as-of a date or timestamp.
3. Warehouse entries, consumption entries, bonded withdrawals (consumption / export / destruction), duty-paid exports, rewarehouse, manufacturing-in-bond, and unused/substitution drawback claims all behave as `docs/WAREHOUSE_RULES.md` specifies — including calendar five-year clocks, FIFO with documented tie-breakers, per-line half-even cent rounding, 99% drawback of duty actually assessed, 8-digit substitution matching, and manufacturing output that inherits the earliest input importation date.
4. `bonded replay EVENTS.json --hts HTS.json --as-of DATE --out DIR` writes the five report JSON files, matching the library.
5. `pytest tests/public` is green. That smoke suite is necessary, not sufficient.

Stdlib only. No network at runtime.

## motivation

Every US importer that runs a CBP bonded warehouse or files unused-merchandise drawback still has a surprising amount of this logic in spreadsheets: which type-21 lot is FIFO, when the five-year clock actually expires (especially around 29 February), whether a type-22 rewarehouse reset the clock (it must not), how manufacturing-in-bond should stamp the output lot, and whether a drawback claim is direct identification or 8-digit substitution. This is the same class of work a senior engineer does when replacing that spreadsheet with a replayable ledger inside a WMS / GTS / Descartes-style product. The task stands in for that job: read an internal legal/ops memo, distrust the prototype, and ship a deterministic kernel finance and the warehouse leads can both live with.

## difficultyExplanation

The difficulty is interacting domain rules, not Python syntax, and the starter actively misleads.

A model that pattern-matches “inventory FIFO” still has to get all of the following right at once:

- Event sourcing vs a mutable register. Reports take `as_of`. A date means end of that UTC day; a timestamp is a cutoff. The intern code ignores `as_of` and replays the whole journal, which is the first thing a hurried agent will keep.
- FIFO is (import_date, entry_number, line_number, lot_id), not LIFO and not “oldest remaining quantity across warehouses.” Same-day lots tie-break on entry number lexicographically. Rewarehouse may not span lots; ordinary withdrawals must split.
- Expired bonded goods sit at the front of FIFO. You do not skip them to pick a newer lot; consumption/export after the last legal day is `LotExpired`. Destruction is the only legal bonded movement the day after the fifth anniversary. Anniversary is calendar years; 2020-02-29 plus five years is 2025-02-28, not 1825 days and not 2025-03-01.
- Duty is `round_half_even(original_entered_value * qty / original_quantity * rate, 2)` per line, independently. Half-even on 1373.625 is 1373.62; half-up is 1373.63. Drawback is 99% of duty actually assessed, then rounded again — 412.50 × 0.99 → 408.38, not a 100% refund and not 99% of entered value.
- Type 32 bonded export never paid duty, so it must not create drawback. Type 01 and type 31 slices do, only after `export_duty_paid`. Substitution matches the first eight HTS digits and may cross country of origin; unused requires 10-digit identity plus COO. A model that treats “export” as one concept will pass public smoke and fail held-out substitution / type-32 cases.
- Manufacturing-in-bond consumes bonded inputs (FIFO per BOM line, reservations so two BOM lines can share a heading), emits `MFG:{ref}` whose entered value is the unrounded sum of input slice values, COO is `MIXED` when inputs disagree, and the clock is the earliest consumed import date. Output duty uses the *output* HTS rate against that inherited value. The intern raises `InvalidBom` for every manufacture.
- Rewarehouse copies the original importation date. The intern stamps the movement date, which silently restarts a five-year clock — a legally catastrophic bug that still produces a plausible inventory.
- `apply` must refuse a bad event without leaving it in the journal. The intern appends first and only blows up later on report, so failed events stick.

Held-out tests cover leap-day clocks, bankers rounding, substitution, manufacturing inheritance, rewarehouse, as-of cutoffs, a conservation fuzzer, and CLI golden reports the intern cannot satisfy. A frontier model that one-shots a clean FIFO kata still has several of these traps left.

## expertTimeEstimateHours

12

## environmentSummary

The sandbox is `python:3.12-slim-bookworm` with `pytest==8.3.4` and `setuptools` baked in at image build (build may use the network; agent and verifier run `no-network`). Workdir is `/app`, which is the Bondwright repo: `docs/WAREHOUSE_RULES.md` (the contract), `docs/API.md`, `data/hts_duty.json`, `examples/newark_week.json`, an installable `bonded` package (intern skeleton), a `bonded` console script, and visible smoke tests under `tests/public`. No extra runtime dependencies are allowed or provided. The sealed grader is copied to `/tests` only at verify time. There is no internet for package installs or ACE/CBP lookups; the HTS table in the tree is the entire tariff.

## resourceEstimate

- cpuMillis: 2000
- memoryMb: 4096
- storageMb: 10240
- gpuCount: 0
- agentTimeoutSec: 14400
- verifierTimeoutSec: 900

## networkRequirements

- mode: none
- justification: The ledger is a pure in-process journal against a baked-in HTS table. Runtime egress would only add non-determinism; image build already installs Python and pytest.

## oracleStrategy

`solution/solve.sh` replaces `/app/bonded` with the reference package. That package is an event-sourced `Ledger`: every `apply` replays the journal into a throwaway state to validate the new event, then appends it. Reports cut the journal at `as_of` and rebuild lots, export slices, and claims. FIFO walks live lots with the documented key; withdrawals split; rewarehouse does not. Duty and drawback use `Decimal` `ROUND_HALF_EVEN` to cents. Manufacturing consumes BOM lines against already-decremented remainders and stamps the output lot with min(import_date) and MIXED COO when needed. The CLI is a thin replay of the same reports. The oracle drives every hidden channel to 1.0.

## verificationStrategy

Visible: `tests/public/test_smoke.py` in the image — HTS canonicalization, FIFO of two lots, 99% drawback on a round type-01 entry, and as-of excluding later rows. The agent is told this is the warehouse leads' smoke suite, not acceptance.

Hidden (sealed under `tests/`, copied in only at grade time): `test.sh` runs `grade.py`, which executes `test_bonded.py` and scores ten channels by pytest class. Channels hit independent angles — behaviour (FIFO splits, specific-id, no partial apply), money (half-even, remaining liability, 99%), clock (inclusive anniversary, Feb 29, expired lots out of liability), drawback kinds (unused vs substitution vs type 32), manufacturing inheritance, rewarehouse clock, as-of + conservation fuzzer, CLI golden files. Reward is the weighted fraction. A canary string in the hidden suite zeros the score if it appears under `/app`.

This measures the objective (a correct replayable ledger) rather than a proxy: every channel drives `Ledger` / CLI on journals the agent never saw.

## binarySuccessCondition

Reward equals 1.0000 — every scoring channel in the hidden suite passes (all 28 held-out tests green). Anything less is not a solved task, even if public smoke is green.

## partialScoreStrategy

Each pytest class is a channel with a fixed weight (surface 0.08, FIFO 0.12, duty 0.10, clock 0.12, unused drawback 0.10, substitution 0.10, manufacture 0.14, rewarehouse 0.08, as-of/invariants 0.10, CLI 0.06). A channel’s score is passed_tests / tests_in_class. Total reward is the sum of weight × channel score, written to `/logs/verifier/reward.txt` and `/logs/verifier/reward.json`. The mapping is monotone: fixing FIFO without drawback still pays the FIFO weight; a full solve is 1.0. The intern skeleton sits near 0.17 (HTS helpers and a couple of accidental duty cases).

## anticipatedExploits

- Hard-code public smoke outputs. Defeated by held-out journals (333-unit half-even line, leap-day clock, substitution across COO, manufacturing MIXED, rewarehouse, conservation fuzzer with a fixed seed but quantities the agent cannot see in `/app`).
- Read `/tests` or copy the canary. Tests are not in the image; if the canary string appears under `/app`, reward is forced to 0.
- Trust the intern skeleton (LIFO, 1825-day clock, 100% drawback, clock reset on rewarehouse, manufacture unimplemented, `as_of` ignored). Hidden channels fail each of those shortcuts independently.
- Game the metric by making CLI echo the API, both wrong. CLI channel asserts golden totals (`3712.50` remaining liability, calendar `days_to_expiry`), not merely CLI↔API equality.
- Invent a network call or extra dependency. Agent/verifier are `no-network`; stdlib only.
- Partial-apply a withdrawal that does not fit, then fix up inventory. `apply` must raise and leave the journal unchanged; a dedicated test checks the lot is untouched.
- Skip expired lots in FIFO to look like a correct clock. Consumption/export of an expired head lot must raise `LotExpired`, not silently pick a newer one.
