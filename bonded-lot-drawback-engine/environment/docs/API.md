# Bondwright API

Package name: `bonded`. Stdlib only.

```python
from bonded import Ledger, HtsTable
from bonded.errors import (
    BondedError,
    DuplicateLot,
    DuplicateRef,
    InsufficientQuantity,
    InvalidBom,
    IllegalTransition,
    LotExpired,
    OverClaim,
    DrawbackWindowClosed,
    UnknownHts,
    UnknownLot,
    UnitMismatch,
)
```

All money in dict reports is a string with two decimal places (e.g. `"412.50"`). Quantities in reports are strings with up to 3 decimal places, trailing zeros stripped but at least one integer digit (`"100"`, `"12.5"`). Dates are `YYYY-MM-DD`. Timestamps in the journal stay as provided.

## `HtsTable`

```python
HtsTable.from_path(path: str | Path) -> HtsTable
HtsTable.from_mapping(data: dict) -> HtsTable
table.rate(hts: str) -> Decimal        # ad valorem fraction
table.canonical(hts: str) -> str       # 4-2-4
table.heading8(hts: str) -> str        # first eight digits, no dots
```

`UnknownHts` if the 10-digit code is missing. JSON file shape:

```json
{
  "6109.10.0012": {"duty_ad_valorem": 0.165, "unit": "doz", "description": "cotton t-shirts"}
}
```

`unit` on the table is informational; the lot's unit is authoritative. A movement that disagrees with the lot unit raises `UnitMismatch`.

## `Ledger`

```python
Ledger(hts: HtsTable)
ledger.apply(event: dict) -> None
ledger.apply_many(events: list[dict]) -> None
```

`apply` appends to the journal and validates immediately (so a bad event fails now, not at report time). `apply_many` is sequential; a failure leaves previously successful events applied.

```python
ledger.inventory(as_of: str) -> list[dict]
ledger.duty_liability(as_of: str) -> dict
ledger.drawback_available(as_of: str) -> dict
ledger.aging(as_of: str) -> list[dict]
ledger.claims(as_of: str) -> list[dict]
```

### inventory row

`lot_id`, `warehouse_id` (or `null`), `hts`, `country_of_origin`, `quantity`, `unit`, `status`, `import_date`, `entry_number` (or `null`), `days_to_expiry` (int or `null`).

### duty_liability

```python
{"total_usd": "4125.00", "lines": [
  {"lot_id": "...", "hts": "...", "quantity": "1000", "duty_usd": "4125.00"}
]}
```

Lines sorted by `lot_id`.

### drawback_available

```python
{"total_usd": "408.38", "lines": [
  {"lot_id": "...", "quantity": "100", "refund_if_claimed_usd": "408.38", "kinds": ["unused"]}
]}
```

`kinds` is always `["unused"]` on this report (direct identification). Substitution across lots is exercised through `drawback_claim`, not this projection. Lines sorted by `lot_id`. Skip zero-quantity lines.

### aging row

`lot_id`, `import_date`, `last_legal_day`, `days_to_expiry`, `quantity`, `warehouse_id`, `hts`.

### claims row

`claim_ref`, `kind`, `quantity`, `refund_usd`, `designated_lot_id`, `export_ref`.

If a claim FIFOs across more than one designated lot, `designated_lot_id` is those ids in consumption order joined by a comma. `export_ref` is the value from the event (or `null` if the event omitted it).

## CLI

Installed as `bonded` and runnable as `python -m bonded`.

```
bonded replay EVENTS.json --hts HTS.json --as-of YYYY-MM-DD --out DIR
```

Writes `inventory.json`, `duty_liability.json`, `drawback_available.json`, `aging.json`, `claims.json` into `DIR` (created if missing). Event file is either a JSON list or `{"events": [...]}`.

Exit 0 on success. Invalid journal / unknown flags / missing files: non-zero, message on stderr.

## Exceptions

All subclass `BondedError`.

| Class | When |
| --- | --- |
| `UnknownHts` | 10-digit code not in the table |
| `UnknownLot` | `lot_id` does not exist or is not live in the place the event requires |
| `DuplicateLot` | lot id already used |
| `DuplicateRef` | `ref` / `claim_ref` reused for a type that must be unique (`manufacture` ref, `drawback_claim` claim_ref). Withdrawal refs may be reused only if you like — they must be unique **per event type + ref** for `manufacture`, `rewarehouse`, `drawback_claim`. Withdrawal `ref` must be unique among withdrawals. |
| `InsufficientQuantity` | FIFO / specific-id cannot fill the quantity |
| `LotExpired` | consumption or export (bonded or duty-paid export does **not** use the warehouse clock; drawback uses the claim window instead) of **bonded** goods after the last legal day. Duty-paid `export_duty_paid` is not clock-blocked. |
| `UnitMismatch` | unit disagrees with the lot |
| `InvalidBom` | empty BOM, or output qty not positive |
| `OverClaim` | drawback quantity larger than unclaimed export or unclaimed designated duty |
| `DrawbackWindowClosed` | claim after designated lot's fifth anniversary |
| `IllegalTransition` | anything else illegal (bad purpose, missing fields, type 32 used as drawback export, substitution/unused match failure, rewarehouse across lots, etc.) |
