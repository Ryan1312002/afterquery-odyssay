# North River Bonded — warehouse rules (Bondwright)

Internal memo. This is the contract for the lot ledger. If software and this memo disagree, the memo wins.

## What we keep

We keep a **journal of events** and we answer questions **as of** a date. The journal order is the order events appear in the file; do not reorder by timestamp. A report `as_of` a calendar date `YYYY-MM-DD` includes every event whose timestamp is on or before the last instant of that UTC day. A report `as_of` a full timestamp includes events with `ts <=` that instant.

We do not talk to ACE. We do not print CBP forms. We track lots, duty that has been assessed, and drawback we are allowed to claim against that duty.

## HTS table

Duty is ad valorem against **entered value**. The table is JSON keyed by a 10-digit HTS, with or without dots. Normalize by stripping dots and spaces; the canonical form we echo in reports is `####.##.####` (4-2-4). Unknown 10-digit codes are an error.

Substitution drawback (below) matches on the **first eight digits** after normalization.

Rates are fractions (0.165 is 16.5%).

## Money and quantity

Use decimal arithmetic. Quantities may have up to 3 decimal places. Money is USD cents.

**Duty on a line** is computed from the **original** lot entered value and **original** lot quantity, then rounded with half-even (banker's) rounding to cents:

```
line_value = original_entered_value * (qty / original_quantity)
duty      = round_half_even(line_value * ad_valorem_rate, 2)
```

Each withdrawal / consumption line is rounded on its own. Do not round the rate, and do not save leftover mills across lines. A 99% drawback refund is `round_half_even(0.99 * duty_being_designated, 2)`.

## Lot identity

| Source | `lot_id` |
| --- | --- |
| `warehouse_entry` or `consumption_entry` | `{entry_number}:{line_number}` |
| `manufacture` output | `MFG:{ref}` |
| `rewarehouse` (the lot that lands in the destination) | `RW:{ref}` |

`entry_number` is an opaque string. `line_number` is a positive integer.

A lot always has: `hts`, `country_of_origin`, `unit`, `original_quantity`, `remaining_quantity`, `original_entered_value_usd`, `import_date`, `entry_number` (if any), `warehouse_id` (null for duty-paid merchandise that is not in the warehouse), `status`.

Status is one of: `bonded`, `duty_paid`, `exported`, `destroyed`. A lot with `remaining_quantity == 0` stays in history but does not appear in on-hand inventory.

## Five-year clock

Clock starts on `import_date` (a calendar date). Merchandise may be withdrawn for consumption or export **through the end of the calendar day of the fifth anniversary**, inclusive. The day after that, the only legal bonded movement is `purpose=destruction`.

Anniversary uses calendar years. 29 February plus five years is 28 February in a non-leap year. Rewarehousing does **not** restart the clock. A manufactured output lot inherits the **earliest** `import_date` among the input lots actually consumed.

`days_to_expiry` on an aging/inventory row is the number of whole days from `as_of`'s calendar date to the last legal consumption/export day (0 on the anniversary; negative if we are looking at a lot that is still on hand after expiry — that can happen if nobody destroyed it).

## FIFO

Unless a movement names `lot_id`, bonded withdrawals, exports from bond, destructions, manufacturing inputs, and rewarehouse picks walk lots that match `warehouse_id + hts + country_of_origin + status=bonded + remaining_quantity > 0`, in order of:

1. `import_date` ascending
2. `entry_number` lexicographic
3. `line_number` ascending
4. `lot_id` lexicographic

Consume from the front until the requested quantity is filled. Splitting a lot is required; never overdraw. If the remainder cannot be filled, fail — do not partially apply the event.

Specific-id (`lot_id` set) must point at a live lot in that warehouse with enough remainder and the same unit. It still has to pass the clock.

Duty-paid exports (see below) FIFO on `status=duty_paid` lots matching `hts + country_of_origin` (warehouse may be null). Same tie-breakers, skipping lots with no remaining quantity.

## Event types

Every event has `type` and `ts` (ISO-8601, UTC, `Z` suffix or offset). Extra keys are ignored.

### `warehouse_entry` (type 21)

Bonded lot. Duty is **not** assessed. Requires: `entry_number`, `line_number`, `warehouse_id`, `hts`, `country_of_origin`, `quantity`, `unit`, `entered_value_usd`, `import_date`. `import_date` is the date of importation for the clock, not necessarily the entry date. Duplicate `lot_id` is an error.

### `consumption_entry` (type 01)

Duty-paid lot, not in the warehouse (`warehouse_id` is null). Duty **is** assessed immediately on the full quantity using the formula above. Same identity keys as a warehouse entry except `warehouse_id`. Eligible later for unused / substitution drawback if the goods are exported.

### `withdrawal`

Bonded lot leaves the warehouse.

- `purpose=consumption` — type 31. Assess duty on the withdrawn quantity. The withdrawn slice becomes a **new** duty-paid lot whose `lot_id` is `DP:{ref}:{source_lot_id}` (if the withdrawal eats several FIFO sources, one duty-paid lot per source slice). `ref` is required.
- `purpose=export` — type 32. No duty. Slice status `exported`. These goods never paid duty, so they do **not** create drawback.
- `purpose=destruction`. No duty. Slice status `destroyed`. Allowed even after expiry.

Requires: `warehouse_id`, `quantity`, `ref`. Either `lot_id` or (`hts` + `country_of_origin`). Unit must match the lot.

### `export_duty_paid`

Physical export of merchandise that already paid duty (a type 01 lot or a type 31 slice). Decreases `duty_paid` remainder. Does not refund anything by itself; it **qualifies** that quantity for a later `drawback_claim`. Requires `quantity`, `ref`, and either `lot_id` or (`hts` + `country_of_origin`).

### `rewarehouse` (type 22)

Move bonded remainder between warehouses. Clock unchanged. Decrease source; create destination lot `RW:{ref}` copying HTS, COO, unit, import_date, entry_number, original quantity/value **of the slice** (the new lot's original_quantity equals the moved quantity; its original_entered_value equals the pro-rata value of that slice: `source.original_entered_value * moved_qty / source.original_quantity`, unrounded except that we store it as a Decimal — reports that need money will round on duty lines, not here). Requires `from_warehouse_id`, `to_warehouse_id`, `quantity`, `ref`, and either `lot_id` or (`hts` + `country_of_origin`). Partial FIFO across multiple sources is **not** allowed for rewarehouse: the quantity must come from a single lot (specific-id or the first FIFO lot, which must have enough remainder).

### `manufacture`

Manufacturing-in-bond. Consume bonded inputs listed in `bom` and create one bonded output lot.

```
bom: [{hts, country_of_origin, quantity, lot_id?}, ...]
```

Each BOM line is a FIFO or specific-id consumption of bonded goods in `warehouse_id` (same rules as a withdrawal). Inputs become `destroyed` slices (process loss / transformation) — they are gone; they do not become duty-paid. Output:

- `lot_id = MFG:{ref}`
- `hts = output_hts`, `quantity = output_quantity`, `unit = output_unit`
- `entered_value_usd` = sum of pro-rata original entered values of consumed input slices (`orig_value * consumed_qty / orig_qty` for each slice, summed, **not** rounded until a later duty line)
- `country_of_origin` = the inputs' COO if they all agree, else `MIXED`
- `import_date` = earliest import_date of consumed input lots
- `warehouse_id` = the manufacturing warehouse
- `status` = `bonded`

Requires `warehouse_id`, `output_hts`, `output_quantity`, `output_unit`, `bom` (non-empty), `ref`. Output HTS must exist in the table. Duplicate `MFG:{ref}` is an error.

### `drawback_claim`

Claim a refund against duty that was actually assessed, for merchandise that has been exported via `export_duty_paid`.

- `kind=unused` — designated import and the export must share **10-digit HTS** and **country of origin**.
- `kind=substitution` — designated import and the export must share the **first eight HTS digits**. COO may differ. `MIXED` is allowed on either side.

`quantity` is the claim quantity. It cannot exceed remaining **unclaimed exported** quantity of matching exports, nor remaining **unclaimed duty-paid designated** quantity.

Designation: if `designate_lot_id` is set, use that duty-paid lot (the original type 01 lot or a `DP:...` slice). Otherwise FIFO across eligible duty-paid lots that still have unclaimed quantity, same tie-breakers as above (import_date, entry_number, line, lot_id).

The export side: if `export_ref` is set, only exported slices with that `ref` count. Otherwise FIFO across all matching unclaimed exports.

Refund is 99% of the duty that was assessed on the designated quantity (pro-rate the lot's assessed duty by `claim_qty / lot.original_quantity` if the lot was a full type 01, or by `claim_qty / slice_original_qty` for a DP slice — in both cases the denominator is the lot's `original_quantity` and the numerator is the claimed qty; duty assessed on the lot is stored at lot creation). Round the refund half-even to cents.

The five-year **claim window** is the same anniversary rule: a claim is refused if `as_of`/`ts` of the claim is after the last legal day of the designated lot's importation anniversary (five years). Window is tested per designated lot.

Cannot claim the same designated quantity twice. Cannot claim against type 32 exports.

Requires `kind`, `quantity`, `claim_ref`, `ts`. Duplicate `claim_ref` is an error.

## Reports

Inventory: on-hand lots with `remaining_quantity > 0` and status `bonded` or `duty_paid`. Sort by `lot_id`. Include `days_to_expiry` for bonded lots (null for duty_paid).

Duty liability: what CBP would be owed if every remaining **bonded** lot were withdrawn for consumption at `as_of` (using that lot's remaining quantity and the duty formula). Skip expired lots — they cannot be withdrawn for consumption, so they are not a consumption liability (they are a destruction candidate). `total_usd` is the sum of rounded line duties (sum of already-rounded lines, not a re-round of the sum).

Drawback available: **direct identification only**. One line per duty-paid lot that still has a positive unclaimed quantity exported **from that same lot** and whose claim window is open at `as_of`.

```
unclaimed_exported_qty = exported_from_this_lot - claimed_against_this_lot
refund_if_claimed_usd  = round_half_even(0.99 * assessed_duty * unclaimed_exported_qty / original_quantity, 2)
kinds                  = ["unused"]
```

`total_usd` is the sum of those already-rounded refunds. Substitution across lots is a `drawback_claim` concern and is **not** projected here. Lines sorted by `lot_id`. Skip lines with quantity 0.

Aging: bonded on-hand lots, sort by last-legal-day ascending then `lot_id`. Fields: `lot_id`, `import_date`, `last_legal_day`, `days_to_expiry`, `quantity`, `warehouse_id`, `hts`.

Claims: every `drawback_claim` applied on or before as_of, in journal order. Fields: `claim_ref`, `kind`, `quantity`, `refund_usd`, `designated_lot_id`, `export_ref` (may be null).

## Errors

Raise the named exceptions in `docs/API.md`. Do not apply part of an event if it fails.
