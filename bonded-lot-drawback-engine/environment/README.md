# Bondwright

Internal lot ledger for North River Bonded (Newark EWR-BOND-4 / JFK-BOND-1).

Read `docs/WAREHOUSE_RULES.md` and `docs/API.md` before touching code. The rules memo is the contract.

```
python -m pytest tests/public
python -m bonded replay examples/newark_week.json --hts data/hts_duty.json --as-of 2024-03-31 --out /tmp/bondwright
```
