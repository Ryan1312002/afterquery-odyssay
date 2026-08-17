"""Event-sourced Bondwright ledger. The warehouse memo is the contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Iterable

from bonded.errors import (
    DrawbackWindowClosed,
    DuplicateLot,
    DuplicateRef,
    IllegalTransition,
    InsufficientQuantity,
    InvalidBom,
    LotExpired,
    OverClaim,
    UnitMismatch,
    UnknownLot,
)
from bonded.hts import HtsTable, canonical_hts, heading8, normalize_hts


CENTS = Decimal("0.01")
QTY = Decimal("0.001")
NINETY_NINE = Decimal("0.99")
UTC = timezone.utc


def parse_ts(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise IllegalTransition("ts is required")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not value:
        raise IllegalTransition("date is required")
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return date.fromisoformat(value[:10])
    return parse_ts(value).date()


def as_of_cutoff(as_of: str) -> datetime:
    raw = str(as_of).strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return datetime.combine(date.fromisoformat(raw), time(23, 59, 59, 999999, tzinfo=UTC))
    return parse_ts(raw)


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def last_legal_day(import_on: date) -> date:
    return add_years(import_on, 5)


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_EVEN)


def fmt_money(value: Decimal) -> str:
    return f"{money(value):.2f}"


def fmt_qty(value: Decimal) -> str:
    q = dec(value).quantize(QTY, rounding=ROUND_HALF_EVEN)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def require(event: dict, *keys: str) -> None:
    for key in keys:
        if event.get(key) in (None, ""):
            raise IllegalTransition(f"missing {key}")


def slice_value(original_value: Decimal, original_qty: Decimal, qty: Decimal) -> Decimal:
    if original_qty == 0:
        raise IllegalTransition("zero original quantity")
    return original_value * qty / original_qty


def line_duty(original_value: Decimal, original_qty: Decimal, qty: Decimal, rate: Decimal) -> Decimal:
    return money(slice_value(original_value, original_qty, qty) * rate)


@dataclass
class Lot:
    lot_id: str
    hts_digits: str
    country_of_origin: str
    unit: str
    original_quantity: Decimal
    remaining_quantity: Decimal
    original_entered_value: Decimal
    import_date: date
    entry_number: str | None
    line_number: int | None
    warehouse_id: str | None
    status: str
    assessed_duty: Decimal = Decimal("0")
    exported_qty: Decimal = Decimal("0")
    claimed_qty: Decimal = Decimal("0")
    designated_qty: Decimal = Decimal("0")

    @property
    def hts(self) -> str:
        return canonical_hts(self.hts_digits)

    def fifo_key(self) -> tuple:
        return (
            self.import_date.isoformat(),
            self.entry_number or "",
            self.line_number or 0,
            self.lot_id,
        )


@dataclass
class ExportSlice:
    ref: str
    lot_id: str
    hts_digits: str
    country_of_origin: str
    quantity: Decimal
    claimed_qty: Decimal
    ts: datetime

    @property
    def unclaimed(self) -> Decimal:
        return self.quantity - self.claimed_qty


@dataclass
class ClaimRecord:
    claim_ref: str
    kind: str
    quantity: Decimal
    refund: Decimal
    designated_lot_id: str
    export_ref: str | None
    ts: datetime


@dataclass
class State:
    lots: dict[str, Lot] = field(default_factory=dict)
    exports: list[ExportSlice] = field(default_factory=list)
    claims: list[ClaimRecord] = field(default_factory=list)
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
        if not isinstance(event, dict):
            raise IllegalTransition("event must be an object")
        require(event, "type", "ts")
        parse_ts(event["ts"])
        probe = State()
        for existing in self._events:
            self._apply_event(probe, existing)
        self._apply_event(probe, event)
        self._events.append(dict(event))

    def apply_many(self, events: list[dict]) -> None:
        for event in events:
            self.apply(event)

    def inventory(self, as_of: str) -> list[dict]:
        state, cutoff = self._state_at(as_of)
        as_of_day = cutoff.date()
        rows = []
        for lot in state.lots.values():
            if lot.remaining_quantity <= 0 or lot.status not in ("bonded", "duty_paid"):
                continue
            days = None
            if lot.status == "bonded":
                days = (last_legal_day(lot.import_date) - as_of_day).days
            rows.append(
                {
                    "lot_id": lot.lot_id,
                    "warehouse_id": lot.warehouse_id,
                    "hts": lot.hts,
                    "country_of_origin": lot.country_of_origin,
                    "quantity": fmt_qty(lot.remaining_quantity),
                    "unit": lot.unit,
                    "status": lot.status,
                    "import_date": lot.import_date.isoformat(),
                    "entry_number": lot.entry_number,
                    "days_to_expiry": days,
                }
            )
        rows.sort(key=lambda r: r["lot_id"])
        return rows

    def duty_liability(self, as_of: str) -> dict:
        state, cutoff = self._state_at(as_of)
        as_of_day = cutoff.date()
        lines = []
        total = Decimal("0.00")
        for lot in state.lots.values():
            if lot.status != "bonded" or lot.remaining_quantity <= 0:
                continue
            if as_of_day > last_legal_day(lot.import_date):
                continue
            duty = line_duty(
                lot.original_entered_value,
                lot.original_quantity,
                lot.remaining_quantity,
                self.hts.rate(lot.hts_digits),
            )
            total += duty
            lines.append(
                {
                    "lot_id": lot.lot_id,
                    "hts": lot.hts,
                    "quantity": fmt_qty(lot.remaining_quantity),
                    "duty_usd": fmt_money(duty),
                }
            )
        lines.sort(key=lambda r: r["lot_id"])
        return {"total_usd": fmt_money(total), "lines": lines}

    def drawback_available(self, as_of: str) -> dict:
        state, cutoff = self._state_at(as_of)
        as_of_day = cutoff.date()
        lines = []
        total = Decimal("0.00")
        for lot in state.lots.values():
            if lot.assessed_duty <= 0:
                continue
            if as_of_day > last_legal_day(lot.import_date):
                continue
            unclaimed = lot.exported_qty - lot.claimed_qty
            if unclaimed <= 0:
                continue
            refund = money(NINETY_NINE * lot.assessed_duty * unclaimed / lot.original_quantity)
            total += refund
            lines.append(
                {
                    "lot_id": lot.lot_id,
                    "quantity": fmt_qty(unclaimed),
                    "refund_if_claimed_usd": fmt_money(refund),
                    "kinds": ["unused"],
                }
            )
        lines.sort(key=lambda r: r["lot_id"])
        return {"total_usd": fmt_money(total), "lines": lines}

    def aging(self, as_of: str) -> list[dict]:
        state, cutoff = self._state_at(as_of)
        as_of_day = cutoff.date()
        rows = []
        for lot in state.lots.values():
            if lot.status != "bonded" or lot.remaining_quantity <= 0:
                continue
            last = last_legal_day(lot.import_date)
            rows.append(
                {
                    "lot_id": lot.lot_id,
                    "import_date": lot.import_date.isoformat(),
                    "last_legal_day": last.isoformat(),
                    "days_to_expiry": (last - as_of_day).days,
                    "quantity": fmt_qty(lot.remaining_quantity),
                    "warehouse_id": lot.warehouse_id,
                    "hts": lot.hts,
                }
            )
        rows.sort(key=lambda r: (r["last_legal_day"], r["lot_id"]))
        return rows

    def claims(self, as_of: str) -> list[dict]:
        state, _cutoff = self._state_at(as_of)
        return [
            {
                "claim_ref": rec.claim_ref,
                "kind": rec.kind,
                "quantity": fmt_qty(rec.quantity),
                "refund_usd": fmt_money(rec.refund),
                "designated_lot_id": rec.designated_lot_id,
                "export_ref": rec.export_ref,
            }
            for rec in state.claims
        ]

    def _state_at(self, as_of: str) -> tuple[State, datetime]:
        cutoff = as_of_cutoff(as_of)
        state = State()
        for event in self._events:
            if parse_ts(event["ts"]) <= cutoff:
                self._apply_event(state, event)
        return state, cutoff

    def _apply_event(self, state: State, event: dict) -> None:
        kind = event["type"]
        ts = parse_ts(event["ts"])
        if kind == "warehouse_entry":
            self._entry(state, event, bonded=True)
        elif kind == "consumption_entry":
            self._entry(state, event, bonded=False)
        elif kind == "withdrawal":
            self._withdrawal(state, event, ts)
        elif kind == "export_duty_paid":
            self._export_duty_paid(state, event, ts)
        elif kind == "rewarehouse":
            self._rewarehouse(state, event, ts)
        elif kind == "manufacture":
            self._manufacture(state, event, ts)
        elif kind == "drawback_claim":
            self._drawback(state, event, ts)
        else:
            raise IllegalTransition(kind)

    def _entry(self, state: State, event: dict, *, bonded: bool) -> None:
        require(
            event,
            "entry_number",
            "line_number",
            "hts",
            "country_of_origin",
            "quantity",
            "unit",
            "entered_value_usd",
            "import_date",
        )
        if bonded:
            require(event, "warehouse_id")
        line_number = int(event["line_number"])
        if line_number < 1:
            raise IllegalTransition("line_number must be positive")
        lot_id = f"{event['entry_number']}:{line_number}"
        if lot_id in state.lots:
            raise DuplicateLot(lot_id)
        qty = dec(event["quantity"])
        if qty <= 0:
            raise IllegalTransition("quantity must be positive")
        value = dec(event["entered_value_usd"])
        if value < 0:
            raise IllegalTransition("entered_value_usd")
        digits = normalize_hts(event["hts"])
        rate = self.hts.rate(digits)
        assessed = Decimal("0")
        if not bonded:
            assessed = line_duty(value, qty, qty, rate)
        state.lots[lot_id] = Lot(
            lot_id=lot_id,
            hts_digits=digits,
            country_of_origin=str(event["country_of_origin"]),
            unit=str(event["unit"]),
            original_quantity=qty,
            remaining_quantity=qty,
            original_entered_value=value,
            import_date=parse_date(event["import_date"]),
            entry_number=str(event["entry_number"]),
            line_number=line_number,
            warehouse_id=str(event["warehouse_id"]) if bonded else None,
            status="bonded" if bonded else "duty_paid",
            assessed_duty=assessed,
        )

    def _take_lots(
        self,
        state: State,
        *,
        qty: Decimal,
        unit: str | None,
        lot_id: str | None,
        warehouse_id: str | None,
        hts: str | None,
        coo: str | None,
        status: str,
        event_day: date,
        allow_expired: bool,
        multi: bool,
    ) -> list[tuple[Lot, Decimal]]:
        if qty <= 0:
            raise IllegalTransition("quantity must be positive")
        if lot_id:
            lot = state.lots.get(lot_id)
            if lot is None or lot.status != status or lot.remaining_quantity <= 0:
                raise UnknownLot(lot_id)
            if warehouse_id is not None and lot.warehouse_id != warehouse_id:
                raise UnknownLot(lot_id)
            self._check_take(lot, qty, unit, event_day, allow_expired)
            lot.remaining_quantity -= qty
            return [(lot, qty)]

        if hts is None or coo is None:
            raise IllegalTransition("hts and country_of_origin required unless lot_id is set")
        digits = normalize_hts(hts)
        candidates = [
            lot
            for lot in state.lots.values()
            if lot.status == status
            and lot.remaining_quantity > 0
            and lot.hts_digits == digits
            and lot.country_of_origin == coo
            and (warehouse_id is None or lot.warehouse_id == warehouse_id)
        ]
        if unit:
            candidates = [lot for lot in candidates if lot.unit == unit]
        candidates.sort(key=lambda lot: lot.fifo_key())
        taken: list[tuple[Lot, Decimal]] = []
        need = qty
        for lot in candidates:
            if need <= 0:
                break
            take = min(lot.remaining_quantity, need)
            if take <= 0:
                continue
            if not multi:
                self._check_take(lot, qty, unit, event_day, allow_expired)
                lot.remaining_quantity -= qty
                return [(lot, qty)]
            self._check_take(lot, take, unit, event_day, allow_expired)
            lot.remaining_quantity -= take
            taken.append((lot, take))
            need -= take
        if need > 0:
            raise InsufficientQuantity(str(qty))
        return taken

    def _check_take(
        self,
        lot: Lot,
        qty: Decimal,
        unit: str | None,
        event_day: date,
        allow_expired: bool,
    ) -> None:
        if unit and lot.unit != unit:
            raise UnitMismatch(lot.unit)
        if lot.remaining_quantity < qty:
            raise InsufficientQuantity(str(qty))
        if not allow_expired and event_day > last_legal_day(lot.import_date):
            raise LotExpired(lot.lot_id)

    def _withdrawal(self, state: State, event: dict, ts: datetime) -> None:
        require(event, "warehouse_id", "quantity", "ref", "purpose")
        ref = str(event["ref"])
        if ref in state.withdrawal_refs:
            raise DuplicateRef(ref)
        purpose = event["purpose"]
        if purpose not in ("consumption", "export", "destruction"):
            raise IllegalTransition(purpose)
        qty = dec(event["quantity"])
        allow_expired = purpose == "destruction"
        slices = self._take_lots(
            state,
            qty=qty,
            unit=event.get("unit"),
            lot_id=event.get("lot_id"),
            warehouse_id=str(event["warehouse_id"]),
            hts=event.get("hts"),
            coo=event.get("country_of_origin"),
            status="bonded",
            event_day=ts.date(),
            allow_expired=allow_expired,
            multi=True,
        )
        state.withdrawal_refs.add(ref)
        for lot, take in slices:
            if purpose == "consumption":
                dp_id = f"DP:{ref}:{lot.lot_id}"
                if dp_id in state.lots:
                    raise DuplicateLot(dp_id)
                duty = line_duty(
                    lot.original_entered_value,
                    lot.original_quantity,
                    take,
                    self.hts.rate(lot.hts_digits),
                )
                state.lots[dp_id] = Lot(
                    lot_id=dp_id,
                    hts_digits=lot.hts_digits,
                    country_of_origin=lot.country_of_origin,
                    unit=lot.unit,
                    original_quantity=take,
                    remaining_quantity=take,
                    original_entered_value=slice_value(
                        lot.original_entered_value, lot.original_quantity, take
                    ),
                    import_date=lot.import_date,
                    entry_number=lot.entry_number,
                    line_number=lot.line_number,
                    warehouse_id=None,
                    status="duty_paid",
                    assessed_duty=duty,
                )
            elif purpose == "export":
                # Bonded export never paid duty; mark remaining-zero lots exported.
                if lot.remaining_quantity == 0:
                    lot.status = "exported"
            elif purpose == "destruction":
                if lot.remaining_quantity == 0:
                    lot.status = "destroyed"

    def _export_duty_paid(self, state: State, event: dict, ts: datetime) -> None:
        require(event, "quantity", "ref")
        ref = str(event["ref"])
        if ref in state.export_refs:
            raise DuplicateRef(ref)
        qty = dec(event["quantity"])
        slices = self._take_lots(
            state,
            qty=qty,
            unit=event.get("unit"),
            lot_id=event.get("lot_id"),
            warehouse_id=None,
            hts=event.get("hts"),
            coo=event.get("country_of_origin"),
            status="duty_paid",
            event_day=ts.date(),
            allow_expired=True,
            multi=True,
        )
        state.export_refs.add(ref)
        for lot, take in slices:
            lot.exported_qty += take
            state.exports.append(
                ExportSlice(
                    ref=ref,
                    lot_id=lot.lot_id,
                    hts_digits=lot.hts_digits,
                    country_of_origin=lot.country_of_origin,
                    quantity=take,
                    claimed_qty=Decimal("0"),
                    ts=ts,
                )
            )

    def _rewarehouse(self, state: State, event: dict, ts: datetime) -> None:
        require(event, "from_warehouse_id", "to_warehouse_id", "quantity", "ref")
        ref = str(event["ref"])
        if ref in state.rewarehouse_refs:
            raise DuplicateRef(ref)
        if event["from_warehouse_id"] == event["to_warehouse_id"]:
            raise IllegalTransition("rewarehouse to same warehouse")
        qty = dec(event["quantity"])
        taken = self._take_lots(
            state,
            qty=qty,
            unit=event.get("unit"),
            lot_id=event.get("lot_id"),
            warehouse_id=str(event["from_warehouse_id"]),
            hts=event.get("hts"),
            coo=event.get("country_of_origin"),
            status="bonded",
            event_day=ts.date(),
            allow_expired=False,
            multi=False,
        )
        lot, take = taken[0]
        new_id = f"RW:{ref}"
        if new_id in state.lots:
            raise DuplicateLot(new_id)
        state.rewarehouse_refs.add(ref)
        state.lots[new_id] = Lot(
            lot_id=new_id,
            hts_digits=lot.hts_digits,
            country_of_origin=lot.country_of_origin,
            unit=lot.unit,
            original_quantity=take,
            remaining_quantity=take,
            original_entered_value=slice_value(
                lot.original_entered_value, lot.original_quantity, take
            ),
            import_date=lot.import_date,
            entry_number=lot.entry_number,
            line_number=lot.line_number,
            warehouse_id=str(event["to_warehouse_id"]),
            status="bonded",
        )

    def _manufacture(self, state: State, event: dict, ts: datetime) -> None:
        require(event, "warehouse_id", "output_hts", "output_quantity", "output_unit", "bom", "ref")
        ref = str(event["ref"])
        if ref in state.manufacture_refs:
            raise DuplicateRef(ref)
        bom = event["bom"]
        if not isinstance(bom, list) or not bom:
            raise InvalidBom("empty bom")
        output_qty = dec(event["output_quantity"])
        if output_qty <= 0:
            raise InvalidBom("output_quantity")
        out_digits = normalize_hts(event["output_hts"])
        self.hts.rate(out_digits)
        consumed: list[tuple[Lot, Decimal]] = []
        for line in bom:
            if not isinstance(line, dict):
                raise InvalidBom("bom line")
            line_qty = dec(line.get("quantity", 0))
            if line_qty <= 0:
                raise InvalidBom("bom quantity")
            taken = self._take_lots(
                state,
                qty=line_qty,
                unit=line.get("unit"),
                lot_id=line.get("lot_id"),
                warehouse_id=str(event["warehouse_id"]),
                hts=line.get("hts"),
                coo=line.get("country_of_origin"),
                status="bonded",
                event_day=ts.date(),
                allow_expired=False,
                multi=True,
            )
            consumed.extend(taken)
        new_id = f"MFG:{ref}"
        if new_id in state.lots:
            raise DuplicateLot(new_id)
        value = Decimal("0")
        coos = set()
        import_dates: list[date] = []
        for lot, take in consumed:
            if lot.remaining_quantity == 0:
                lot.status = "destroyed"
            value += slice_value(lot.original_entered_value, lot.original_quantity, take)
            coos.add(lot.country_of_origin)
            import_dates.append(lot.import_date)
        coo = next(iter(coos)) if len(coos) == 1 else "MIXED"
        state.manufacture_refs.add(ref)
        state.lots[new_id] = Lot(
            lot_id=new_id,
            hts_digits=out_digits,
            country_of_origin=coo,
            unit=str(event["output_unit"]),
            original_quantity=output_qty,
            remaining_quantity=output_qty,
            original_entered_value=value,
            import_date=min(import_dates),
            entry_number=None,
            line_number=None,
            warehouse_id=str(event["warehouse_id"]),
            status="bonded",
        )

    def _matches(self, lot: Lot, slc: ExportSlice, kind: str) -> bool:
        if kind == "unused":
            return lot.hts_digits == slc.hts_digits and lot.country_of_origin == slc.country_of_origin
        if kind == "substitution":
            return heading8(lot.hts_digits) == heading8(slc.hts_digits)
        raise IllegalTransition(kind)

    def _drawback(self, state: State, event: dict, ts: datetime) -> None:
        require(event, "kind", "quantity", "claim_ref")
        kind = event["kind"]
        if kind not in ("unused", "substitution"):
            raise IllegalTransition(kind)
        claim_ref = str(event["claim_ref"])
        if claim_ref in state.claim_refs:
            raise DuplicateRef(claim_ref)
        qty = dec(event["quantity"])
        if qty <= 0:
            raise IllegalTransition("quantity must be positive")
        export_ref = event.get("export_ref")
        exports = [
            slc
            for slc in state.exports
            if slc.unclaimed > 0 and (export_ref in (None, "") or slc.ref == export_ref)
        ]
        exports.sort(key=lambda slc: (slc.ts, slc.ref, slc.lot_id))
        designate_id = event.get("designate_lot_id")
        if designate_id:
            lot = state.lots.get(designate_id)
            if lot is None or lot.assessed_duty <= 0:
                raise UnknownLot(str(designate_id))
            lots = [lot]
        else:
            lots = [lot for lot in state.lots.values() if lot.assessed_duty > 0]
            lots.sort(key=lambda lot: lot.fifo_key())

        remaining = qty
        used_lots: list[str] = []
        refund = Decimal("0.00")
        for lot in lots:
            if remaining <= 0:
                break
            undesignated = lot.original_quantity - lot.designated_qty
            if undesignated <= 0:
                continue
            if ts.date() > last_legal_day(lot.import_date):
                if designate_id:
                    raise DrawbackWindowClosed(lot.lot_id)
                continue
            matched_export = Decimal("0")
            for slc in exports:
                if remaining <= 0 or undesignated <= 0:
                    break
                if slc.unclaimed <= 0 or not self._matches(lot, slc, kind):
                    continue
                take = min(remaining, undesignated, slc.unclaimed)
                slc.claimed_qty += take
                lot.designated_qty += take
                lot.claimed_qty += take
                piece = money(NINETY_NINE * lot.assessed_duty * take / lot.original_quantity)
                refund += piece
                remaining -= take
                undesignated -= take
                matched_export += take
            if matched_export > 0:
                used_lots.append(lot.lot_id)
        if remaining > 0:
            # Distinguish match failure from quantity shortage when explicitly designated.
            if designate_id:
                lot = state.lots[str(designate_id)]
                if ts.date() > last_legal_day(lot.import_date):
                    raise DrawbackWindowClosed(lot.lot_id)
                matching = [slc for slc in exports if self._matches(lot, slc, kind)]
                if not matching:
                    raise IllegalTransition("drawback match failed")
            raise OverClaim(str(qty))
        state.claim_refs.add(claim_ref)
        state.claims.append(
            ClaimRecord(
                claim_ref=claim_ref,
                kind=kind,
                quantity=qty,
                refund=refund,
                designated_lot_id=",".join(used_lots),
                export_ref=str(export_ref) if export_ref not in (None, "") else None,
                ts=ts,
            )
        )


def iter_on_hand(state: State) -> Iterable[Lot]:
    for lot in state.lots.values():
        if lot.remaining_quantity > 0 and lot.status in ("bonded", "duty_paid"):
            yield lot
