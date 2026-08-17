"""Intern sprint skeleton. Looks wired up; the math is not signed off."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from bonded.errors import (
    DuplicateLot,
    DuplicateRef,
    IllegalTransition,
    InsufficientQuantity,
    InvalidBom,
    UnknownLot,
    UnitMismatch,
)
from bonded.hts import HtsTable, canonical_hts


CENTS = Decimal("0.01")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _fmt_money(value: Decimal) -> str:
    return f"{_money(value):.2f}"


def _fmt_qty(value: Decimal) -> str:
    q = Decimal(value)
    s = format(q.quantize(Decimal("0.001")), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


@dataclass
class _Lot:
    lot_id: str
    hts: str
    country_of_origin: str
    unit: str
    original_quantity: Decimal
    remaining_quantity: Decimal
    original_entered_value: Decimal
    import_date: str
    entry_number: str | None
    line_number: int | None
    warehouse_id: str | None
    status: str
    assessed_duty: Decimal = Decimal("0")
    exported_qty: Decimal = Decimal("0")
    claimed_qty: Decimal = Decimal("0")
    created_ts: str = ""


@dataclass
class _State:
    lots: dict[str, _Lot] = field(default_factory=dict)
    claims: list[dict[str, Any]] = field(default_factory=list)
    withdrawal_refs: set[str] = field(default_factory=set)
    manufacture_refs: set[str] = field(default_factory=set)
    rewarehouse_refs: set[str] = field(default_factory=set)
    claim_refs: set[str] = field(default_factory=set)
    export_refs: set[str] = field(default_factory=set)


class Ledger:
    def __init__(self, hts: HtsTable):
        self.hts = hts
        self._events: list[dict[str, Any]] = []

    def apply(self, event: dict) -> None:
        if not isinstance(event, dict) or "type" not in event or "ts" not in event:
            raise IllegalTransition("event requires type and ts")
        self._events.append(dict(event))

    def apply_many(self, events: list[dict]) -> None:
        for event in events:
            self.apply(event)

    def inventory(self, as_of: str) -> list[dict]:
        state = self._rebuild()
        rows = []
        for lot in state.lots.values():
            if lot.remaining_quantity <= 0:
                continue
            if lot.status not in ("bonded", "duty_paid"):
                continue
            rows.append(
                {
                    "lot_id": lot.lot_id,
                    "warehouse_id": lot.warehouse_id,
                    "hts": canonical_hts(lot.hts) if lot.hts[4:5] != "." else lot.hts,
                    "country_of_origin": lot.country_of_origin,
                    "quantity": _fmt_qty(lot.remaining_quantity),
                    "unit": lot.unit,
                    "status": lot.status,
                    "import_date": lot.import_date,
                    "entry_number": lot.entry_number,
                    "days_to_expiry": 1825,
                }
            )
        rows.sort(key=lambda r: r["lot_id"])
        return rows

    def duty_liability(self, as_of: str) -> dict:
        state = self._rebuild()
        lines = []
        total = Decimal("0")
        for lot in state.lots.values():
            if lot.status != "bonded" or lot.remaining_quantity <= 0:
                continue
            duty = _money(
                lot.original_entered_value
                * lot.remaining_quantity
                / lot.original_quantity
                * self.hts.rate(lot.hts)
            )
            total += duty
            lines.append(
                {
                    "lot_id": lot.lot_id,
                    "hts": self.hts.canonical(lot.hts),
                    "quantity": _fmt_qty(lot.remaining_quantity),
                    "duty_usd": _fmt_money(duty),
                }
            )
        lines.sort(key=lambda r: r["lot_id"])
        return {"total_usd": _fmt_money(total), "lines": lines}

    def drawback_available(self, as_of: str) -> dict:
        state = self._rebuild()
        lines = []
        total = Decimal("0")
        for lot in state.lots.values():
            qty = lot.exported_qty - lot.claimed_qty
            if qty <= 0:
                continue
            refund = _money(lot.assessed_duty * qty / lot.original_quantity)
            total += refund
            lines.append(
                {
                    "lot_id": lot.lot_id,
                    "quantity": _fmt_qty(qty),
                    "refund_if_claimed_usd": _fmt_money(refund),
                    "kinds": ["unused"],
                }
            )
        lines.sort(key=lambda r: r["lot_id"])
        return {"total_usd": _fmt_money(total), "lines": lines}

    def aging(self, as_of: str) -> list[dict]:
        state = self._rebuild()
        rows = []
        for lot in state.lots.values():
            if lot.status != "bonded" or lot.remaining_quantity <= 0:
                continue
            rows.append(
                {
                    "lot_id": lot.lot_id,
                    "import_date": lot.import_date,
                    "last_legal_day": lot.import_date,
                    "days_to_expiry": 1825,
                    "quantity": _fmt_qty(lot.remaining_quantity),
                    "warehouse_id": lot.warehouse_id,
                    "hts": self.hts.canonical(lot.hts),
                }
            )
        rows.sort(key=lambda r: (r["last_legal_day"], r["lot_id"]))
        return rows

    def claims(self, as_of: str) -> list[dict]:
        return list(self._rebuild().claims)

    def _rebuild(self) -> _State:
        # Intentionally ignores as-of: intern thought the journal was a live register.
        state = _State()
        for event in self._events:
            self._apply_event(state, event)
        return state

    def _apply_event(self, state: _State, event: dict) -> None:
        kind = event["type"]
        if kind == "warehouse_entry":
            lot_id = f"{event['entry_number']}:{event['line_number']}"
            if lot_id in state.lots:
                raise DuplicateLot(lot_id)
            qty = Decimal(str(event["quantity"]))
            state.lots[lot_id] = _Lot(
                lot_id=lot_id,
                hts=event["hts"],
                country_of_origin=event["country_of_origin"],
                unit=event["unit"],
                original_quantity=qty,
                remaining_quantity=qty,
                original_entered_value=Decimal(str(event["entered_value_usd"])),
                import_date=event["import_date"],
                entry_number=event["entry_number"],
                line_number=int(event["line_number"]),
                warehouse_id=event["warehouse_id"],
                status="bonded",
                created_ts=event["ts"],
            )
        elif kind == "consumption_entry":
            lot_id = f"{event['entry_number']}:{event['line_number']}"
            if lot_id in state.lots:
                raise DuplicateLot(lot_id)
            qty = Decimal(str(event["quantity"]))
            value = Decimal(str(event["entered_value_usd"]))
            duty = _money(value * self.hts.rate(event["hts"]))
            state.lots[lot_id] = _Lot(
                lot_id=lot_id,
                hts=event["hts"],
                country_of_origin=event["country_of_origin"],
                unit=event["unit"],
                original_quantity=qty,
                remaining_quantity=qty,
                original_entered_value=value,
                import_date=event["import_date"],
                entry_number=event["entry_number"],
                line_number=int(event["line_number"]),
                warehouse_id=None,
                status="duty_paid",
                assessed_duty=duty,
                created_ts=event["ts"],
            )
        elif kind == "withdrawal":
            ref = event.get("ref")
            if not ref:
                raise IllegalTransition("withdrawal requires ref")
            if ref in state.withdrawal_refs:
                raise DuplicateRef(ref)
            state.withdrawal_refs.add(ref)
            qty = Decimal(str(event["quantity"]))
            lot = self._pick_lot(
                state,
                warehouse_id=event.get("warehouse_id"),
                hts=event.get("hts"),
                coo=event.get("country_of_origin"),
                lot_id=event.get("lot_id"),
                qty=qty,
                unit=event.get("unit"),
                lifo=True,
            )
            if lot.unit and event.get("unit") and lot.unit != event["unit"]:
                raise UnitMismatch(lot.unit)
            if lot.remaining_quantity < qty:
                raise InsufficientQuantity(str(qty))
            lot.remaining_quantity -= qty
            purpose = event.get("purpose")
            if purpose == "consumption":
                dp_id = f"DP:{ref}:{lot.lot_id}"
                slice_val = lot.original_entered_value * qty / lot.original_quantity
                duty = _money(slice_val * self.hts.rate(lot.hts))
                state.lots[dp_id] = _Lot(
                    lot_id=dp_id,
                    hts=lot.hts,
                    country_of_origin=lot.country_of_origin,
                    unit=lot.unit,
                    original_quantity=qty,
                    remaining_quantity=qty,
                    original_entered_value=slice_val,
                    import_date=lot.import_date,
                    entry_number=lot.entry_number,
                    line_number=lot.line_number,
                    warehouse_id=None,
                    status="duty_paid",
                    assessed_duty=duty,
                    created_ts=event["ts"],
                )
            elif purpose == "export":
                lot.status = "exported" if lot.remaining_quantity == 0 else lot.status
            elif purpose == "destruction":
                lot.status = "destroyed" if lot.remaining_quantity == 0 else lot.status
            else:
                raise IllegalTransition(purpose)
        elif kind == "export_duty_paid":
            ref = event.get("ref")
            if not ref:
                raise IllegalTransition("export_duty_paid requires ref")
            if ref in state.export_refs:
                raise DuplicateRef(ref)
            state.export_refs.add(ref)
            qty = Decimal(str(event["quantity"]))
            lot = self._pick_lot(
                state,
                warehouse_id=None,
                hts=event.get("hts"),
                coo=event.get("country_of_origin"),
                lot_id=event.get("lot_id"),
                qty=qty,
                unit=event.get("unit"),
                lifo=True,
                status="duty_paid",
            )
            if lot.remaining_quantity < qty:
                raise InsufficientQuantity(str(qty))
            lot.remaining_quantity -= qty
            lot.exported_qty += qty
        elif kind == "rewarehouse":
            ref = event.get("ref")
            if not ref:
                raise IllegalTransition("rewarehouse requires ref")
            if ref in state.rewarehouse_refs:
                raise DuplicateRef(ref)
            state.rewarehouse_refs.add(ref)
            qty = Decimal(str(event["quantity"]))
            lot = self._pick_lot(
                state,
                warehouse_id=event.get("from_warehouse_id"),
                hts=event.get("hts"),
                coo=event.get("country_of_origin"),
                lot_id=event.get("lot_id"),
                qty=qty,
                unit=None,
                lifo=True,
            )
            if lot.remaining_quantity < qty:
                raise InsufficientQuantity(str(qty))
            lot.remaining_quantity -= qty
            new_id = f"RW:{ref}"
            slice_val = lot.original_entered_value * qty / lot.original_quantity
            # Clock reset — intern used the movement date as a new importation.
            state.lots[new_id] = _Lot(
                lot_id=new_id,
                hts=lot.hts,
                country_of_origin=lot.country_of_origin,
                unit=lot.unit,
                original_quantity=qty,
                remaining_quantity=qty,
                original_entered_value=slice_val,
                import_date=event["ts"][:10],
                entry_number=lot.entry_number,
                line_number=lot.line_number,
                warehouse_id=event.get("to_warehouse_id"),
                status="bonded",
                created_ts=event["ts"],
            )
        elif kind == "manufacture":
            raise InvalidBom("manufacturing-in-bond not implemented")
        elif kind == "drawback_claim":
            ref = event.get("claim_ref")
            if not ref:
                raise IllegalTransition("drawback_claim requires claim_ref")
            if ref in state.claim_refs:
                raise DuplicateRef(ref)
            state.claim_refs.add(ref)
            qty = Decimal(str(event["quantity"]))
            target = None
            for lot in state.lots.values():
                if lot.exported_qty - lot.claimed_qty >= qty:
                    target = lot
                    break
            if target is None:
                raise InsufficientQuantity(str(qty))
            target.claimed_qty += qty
            refund = _money(target.assessed_duty * qty / target.original_quantity)
            state.claims.append(
                {
                    "claim_ref": ref,
                    "kind": event.get("kind") or "unused",
                    "quantity": _fmt_qty(qty),
                    "refund_usd": _fmt_money(refund),
                    "designated_lot_id": target.lot_id,
                    "export_ref": event.get("export_ref"),
                }
            )
        else:
            raise IllegalTransition(kind)

    def _pick_lot(
        self,
        state: _State,
        *,
        warehouse_id: str | None,
        hts: str | None,
        coo: str | None,
        lot_id: str | None,
        qty: Decimal,
        unit: str | None,
        lifo: bool,
        status: str = "bonded",
    ) -> _Lot:
        if lot_id:
            lot = state.lots.get(lot_id)
            if lot is None:
                raise UnknownLot(lot_id)
            return lot
        candidates = []
        for lot in state.lots.values():
            if lot.status != status or lot.remaining_quantity <= 0:
                continue
            if warehouse_id is not None and lot.warehouse_id != warehouse_id:
                continue
            if hts is not None and lot.hts.replace(".", "") != str(hts).replace(".", ""):
                continue
            if coo is not None and lot.country_of_origin != coo:
                continue
            candidates.append(lot)
        if not candidates:
            raise InsufficientQuantity(str(qty))
        candidates.sort(key=lambda lot: (lot.import_date, lot.lot_id), reverse=lifo)
        return candidates[0]


def _days_to_expiry(import_date: str, as_of: str) -> int:
    start = datetime.fromisoformat(import_date)
    end = datetime.fromisoformat(as_of[:10])
    return 1825 - int((end - start) / timedelta(days=1))
