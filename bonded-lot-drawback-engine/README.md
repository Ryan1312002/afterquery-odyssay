# Bonded lot ledger and duty-drawback engine

Harbor / Odyssey task. The agent sees only `environment/` (mounted at `/app`) plus `instruction.md`. `tests/` and `solution/` are sealed.

## What the agent does

Finish **Bondwright**, North River Bonded's lot ledger: type-21 warehouse entries, FIFO withdrawals against a five-year importation clock, manufacturing-in-bond, unused and substitution drawback, and as-of reports. The intern skeleton in `bonded/` is the wrong shape of right — the warehouse memo is the contract.

## Environment

- Image: `python:3.12-slim-bookworm`
- Offline at agent and verifier time (`network_mode = "no-network"`)
- 2 CPUs / 4096 MB / 10240 MB disk
- Agent budget 4h, verifier 15m
- `pytest` and the editable `bonded` package are preinstalled

## Verifier

`tests/test.sh` runs `grade.py`, which executes the held-out suite and writes a weighted reward to `/logs/verifier/reward.txt`.

| Channel | Weight | Measures |
| --- | --- | --- |
| TestSurface | 0.08 | package, HTS, immediate validation, calendar clock |
| TestFifo | 0.12 | split lots, tie-break, specific-id, no partial apply |
| TestDutyRounding | 0.10 | half-even cents, remaining liability |
| TestClock | 0.12 | inclusive anniversary, Feb 29, expired liability |
| TestDrawbackUnused | 0.10 | 99% refund, window, type 32 is not drawback |
| TestSubstitution | 0.10 | 8-digit HTS, COO may differ |
| TestManufacture | 0.14 | inherited clock, MIXED COO, output duty |
| TestRewarehouse | 0.08 | clock preserved, no multi-lot move |
| TestAsOfAndInvariants | 0.10 | cutoff, conservation, failed events |
| TestCliParity | 0.06 | CLI golden reports |

Visible smoke tests live in the image at `tests/public`. Oracle reward is 1.0; untouched skeleton sits near 0.17.

## Layout

```
bonded-lot-drawback-engine/
├── task.toml
├── instruction.md
├── environment/          # → /app
├── tests/                # sealed grader
└── solution/             # oracle
```
