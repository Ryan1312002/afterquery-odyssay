# CANARY: odyssey-bonded-lot-drawback-engine-canary-7f3c1e
# Held-out grader. Do not copy this file into /app.

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import pytest

from bonded import (
    DrawbackWindowClosed,
    DuplicateLot,
    HtsTable,
    IllegalTransition,
    InsufficientQuantity,
    Ledger,
    LotExpired,
    OverClaim,
    UnknownHts,
    UnknownLot,
)
from bonded.errors import InvalidBom


APP = Path(os.environ.get("BONDED_APP", "/app"))
HTS_PATH = APP / "data" / "hts_duty.json"
HTS = HtsTable.from_path(HTS_PATH)
CENTS = Decimal("0.01")


def D(x) -> Decimal:
    return Decimal(str(x))


def money(x) -> Decimal:
    return D(x).quantize(CENTS, rounding=ROUND_HALF_EVEN)


def warehouse_entry(**kwargs):
    base = {
        "type": "warehouse_entry",
        "ts": "2024-01-01T00:00:00Z",
        "entry_number": "E-1",
        "line_number": 1,
        "warehouse_id": "EWR-BOND-4",
        "hts": "6109.10.0012",
        "country_of_origin": "VN",
        "quantity": 1000,
        "unit": "doz",
        "entered_value_usd": "25000",
        "import_date": "2024-01-01",
    }
    base.update(kwargs)
    return base


def consumption_entry(**kwargs):
    base = {
        "type": "consumption_entry",
        "ts": "2024-01-01T00:00:00Z",
        "entry_number": "C-1",
        "line_number": 1,
        "hts": "6109.10.0012",
        "country_of_origin": "VN",
        "quantity": 100,
        "unit": "doz",
        "entered_value_usd": "2500",
        "import_date": "2024-01-01",
    }
    base.update(kwargs)
    return base


class TestSurface:
    def test_package_exports(self):
        import bonded

        assert hasattr(bonded, "Ledger")
        assert hasattr(bonded, "HtsTable")
        assert issubclass(UnknownHts, bonded.BondedError)

    def test_unknown_hts(self):
        with pytest.raises(UnknownHts):
            HTS.rate("9999.99.9999")

    def test_duplicate_lot(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry())
        with pytest.raises(DuplicateLot):
            ledger.apply(warehouse_entry(ts="2024-01-02T00:00:00Z"))

    def test_calendar_clock_not_fixed_1825(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(import_date="2024-01-01", quantity=1, entered_value_usd="10"))
        row = ledger.inventory("2024-01-01")[0]
        expected = (date(2029, 1, 1) - date(2024, 1, 1)).days
        assert row["days_to_expiry"] == expected
        assert expected != 1825


class TestFifo:
    def test_split_across_two_bonded_lots(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(entry_number="E-A", quantity=6, entered_value_usd="600"))
        ledger.apply(
            warehouse_entry(
                ts="2024-01-02T00:00:00Z",
                entry_number="E-B",
                quantity=6,
                entered_value_usd="900",
                import_date="2024-02-01",
            )
        )
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-03-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 8,
                "ref": "WD-SPLIT",
            }
        )
        inv = {row["lot_id"]: row for row in ledger.inventory("2024-03-01")}
        assert "E-A:1" not in inv
        assert inv["E-B:1"]["quantity"] == "4"

    def test_same_day_entry_number_tiebreak(self):
        ledger = Ledger(HTS)
        ledger.apply(
            warehouse_entry(entry_number="E-Z", quantity=5, entered_value_usd="100", import_date="2024-05-01")
        )
        ledger.apply(
            warehouse_entry(
                ts="2024-05-01T12:00:00Z",
                entry_number="E-A",
                quantity=5,
                entered_value_usd="100",
                import_date="2024-05-01",
            )
        )
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-06-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 5,
                "ref": "WD-TIE",
            }
        )
        inv = {row["lot_id"]: row for row in ledger.inventory("2024-06-01")}
        assert "E-A:1" not in inv
        assert inv["E-Z:1"]["quantity"] == "5"

    def test_specific_id_skips_fifo(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(entry_number="E-OLD", quantity=5, entered_value_usd="50"))
        ledger.apply(
            warehouse_entry(
                ts="2024-01-02T00:00:00Z",
                entry_number="E-NEW",
                quantity=5,
                entered_value_usd="50",
                import_date="2024-06-01",
            )
        )
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-07-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "lot_id": "E-NEW:1",
                "quantity": 5,
                "ref": "WD-SPEC",
            }
        )
        inv = {row["lot_id"]: row for row in ledger.inventory("2024-07-01")}
        assert "E-NEW:1" not in inv
        assert inv["E-OLD:1"]["quantity"] == "5"

    def test_insufficient_does_not_partially_apply(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(quantity=3, entered_value_usd="30"))
        with pytest.raises(InsufficientQuantity):
            ledger.apply(
                {
                    "type": "withdrawal",
                    "ts": "2024-02-01T00:00:00Z",
                    "warehouse_id": "EWR-BOND-4",
                    "purpose": "destruction",
                    "hts": "6109.10.0012",
                    "country_of_origin": "VN",
                    "quantity": 4,
                    "ref": "WD-TOO-MUCH",
                }
            )
        assert ledger.inventory("2024-02-01")[0]["quantity"] == "3"


class TestDutyRounding:
    def test_independent_line_rounding(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry())
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-01-02T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "consumption",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 333,
                "ref": "WD-333",
            }
        )
        # 25000 * 333/1000 * 0.165 = 1373.625 -> 1373.62
        dp = [r for r in ledger.inventory("2024-01-02") if r["lot_id"].startswith("DP:")][0]
        assert dp["quantity"] == "333"
        ledger.apply(
            {
                "type": "export_duty_paid",
                "ts": "2024-01-03T00:00:00Z",
                "lot_id": "DP:WD-333:E-1:1",
                "quantity": 333,
                "ref": "EXP-333",
            }
        )
        avail = ledger.drawback_available("2024-01-03")
        assessed = money(D("25000") * D("333") / D("1000") * D("0.165"))
        assert assessed == D("1373.62")
        assert avail["lines"][0]["refund_if_claimed_usd"] == f"{money(D('0.99') * assessed):.2f}"

    def test_zero_rate_furniture(self):
        ledger = Ledger(HTS)
        ledger.apply(
            warehouse_entry(
                hts="9403.60.8081",
                unit="ea",
                quantity=12,
                entered_value_usd="4800",
                country_of_origin="IT",
            )
        )
        liab = ledger.duty_liability("2024-01-01")
        assert liab["total_usd"] == "0.00"

    def test_liability_uses_remaining_qty(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(quantity=10, entered_value_usd="1000", hts="8504.40.9570", unit="ea", country_of_origin="CN"))
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-01-02T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "hts": "8504.40.9570",
                "country_of_origin": "CN",
                "quantity": 4,
                "ref": "WD-CV",
            }
        )
        # remaining 6/10 of 1000 * 0.013 = 7.80
        assert ledger.duty_liability("2024-01-02")["total_usd"] == "7.80"


class TestClock:
    def test_anniversary_inclusive(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(import_date="2020-03-15", quantity=2, entered_value_usd="20"))
        aging = ledger.aging("2025-03-15")
        assert aging[0]["last_legal_day"] == "2025-03-15"
        assert aging[0]["days_to_expiry"] == 0
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2025-03-15T23:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "export",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 1,
                "ref": "WD-ON-DAY",
            }
        )
        with pytest.raises(LotExpired):
            ledger.apply(
                {
                    "type": "withdrawal",
                    "ts": "2025-03-16T00:00:00Z",
                    "warehouse_id": "EWR-BOND-4",
                    "purpose": "export",
                    "hts": "6109.10.0012",
                    "country_of_origin": "VN",
                    "quantity": 1,
                    "ref": "WD-LATE",
                }
            )
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2025-03-16T00:00:01Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 1,
                "ref": "WD-KILL",
            }
        )

    def test_feb29_rolls_to_feb28(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(import_date="2020-02-29", quantity=1, entered_value_usd="10"))
        aging = ledger.aging("2025-02-28")
        assert aging[0]["last_legal_day"] == "2025-02-28"
        assert aging[0]["days_to_expiry"] == 0

    def test_expired_bonded_not_in_liability(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(import_date="2018-01-01", quantity=4, entered_value_usd="400"))
        liab = ledger.duty_liability("2024-01-02")
        assert liab["total_usd"] == "0.00"
        assert liab["lines"] == []
        inv = ledger.inventory("2024-01-02")
        assert inv[0]["days_to_expiry"] < 0


class TestDrawbackUnused:
    def test_type01_full_claim(self):
        ledger = Ledger(HTS)
        ledger.apply(consumption_entry())
        ledger.apply(
            {
                "type": "export_duty_paid",
                "ts": "2024-02-01T00:00:00Z",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 100,
                "ref": "EXP-1",
            }
        )
        avail = ledger.drawback_available("2024-02-01")
        assert avail["total_usd"] == "408.38"
        ledger.apply(
            {
                "type": "drawback_claim",
                "ts": "2024-02-02T00:00:00Z",
                "kind": "unused",
                "quantity": 100,
                "export_ref": "EXP-1",
                "claim_ref": "DBK-1",
            }
        )
        claim = ledger.claims("2024-02-02")[0]
        assert claim["refund_usd"] == "408.38"
        assert claim["designated_lot_id"] == "C-1:1"
        assert ledger.drawback_available("2024-02-02")["lines"] == []

    def test_overclaim_and_window(self):
        ledger = Ledger(HTS)
        ledger.apply(consumption_entry(import_date="2019-01-01", ts="2019-01-01T00:00:00Z"))
        ledger.apply(
            {
                "type": "export_duty_paid",
                "ts": "2019-06-01T00:00:00Z",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 100,
                "ref": "EXP-OLD",
            }
        )
        with pytest.raises(DrawbackWindowClosed):
            ledger.apply(
                {
                    "type": "drawback_claim",
                    "ts": "2024-01-02T00:00:00Z",
                    "kind": "unused",
                    "quantity": 10,
                    "designate_lot_id": "C-1:1",
                    "claim_ref": "DBK-LATE",
                }
            )
        ledger = Ledger(HTS)
        ledger.apply(consumption_entry())
        ledger.apply(
            {
                "type": "export_duty_paid",
                "ts": "2024-02-01T00:00:00Z",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 10,
                "ref": "EXP-SMALL",
            }
        )
        with pytest.raises(OverClaim):
            ledger.apply(
                {
                    "type": "drawback_claim",
                    "ts": "2024-02-02T00:00:00Z",
                    "kind": "unused",
                    "quantity": 11,
                    "claim_ref": "DBK-BIG",
                }
            )

    def test_type32_does_not_create_drawback(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(quantity=10, entered_value_usd="100"))
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-02-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "export",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 10,
                "ref": "WD-32",
            }
        )
        assert ledger.drawback_available("2024-02-01")["lines"] == []
        with pytest.raises((OverClaim, IllegalTransition, InsufficientQuantity, UnknownLot)):
            ledger.apply(
                {
                    "type": "drawback_claim",
                    "ts": "2024-02-02T00:00:00Z",
                    "kind": "unused",
                    "quantity": 10,
                    "export_ref": "WD-32",
                    "claim_ref": "DBK-32",
                }
            )


class TestSubstitution:
    def test_eight_digit_match_across_coo(self):
        ledger = Ledger(HTS)
        ledger.apply(
            consumption_entry(
                entry_number="MEN",
                hts="6109.10.0012",
                country_of_origin="VN",
                quantity=40,
                entered_value_usd="1000",
            )
        )
        ledger.apply(
            consumption_entry(
                ts="2024-01-02T00:00:00Z",
                entry_number="WMN",
                hts="6109.10.0027",
                country_of_origin="BD",
                quantity=40,
                entered_value_usd="1100",
                import_date="2024-01-02",
            )
        )
        ledger.apply(
            {
                "type": "export_duty_paid",
                "ts": "2024-02-01T00:00:00Z",
                "hts": "6109.10.0027",
                "country_of_origin": "BD",
                "quantity": 40,
                "ref": "EXP-WMN",
            }
        )
        with pytest.raises(IllegalTransition):
            ledger.apply(
                {
                    "type": "drawback_claim",
                    "ts": "2024-02-02T00:00:00Z",
                    "kind": "unused",
                    "quantity": 40,
                    "designate_lot_id": "MEN:1",
                    "export_ref": "EXP-WMN",
                    "claim_ref": "DBK-UNUSED-FAIL",
                }
            )
        ledger.apply(
            {
                "type": "drawback_claim",
                "ts": "2024-02-03T00:00:00Z",
                "kind": "substitution",
                "quantity": 40,
                "designate_lot_id": "MEN:1",
                "export_ref": "EXP-WMN",
                "claim_ref": "DBK-SUB",
            }
        )
        # duty on MEN: 1000 * 0.165 = 165.00; 99% = 163.35
        assert ledger.claims("2024-02-03")[0]["refund_usd"] == "163.35"
        assert ledger.claims("2024-02-03")[0]["designated_lot_id"] == "MEN:1"


class TestManufacture:
    def test_inherits_earliest_clock_and_mixed_coo(self):
        ledger = Ledger(HTS)
        ledger.apply(
            warehouse_entry(
                entry_number="FAB-VN",
                hts="6006.22.1000",
                unit="kg",
                quantity=50,
                entered_value_usd="2000",
                country_of_origin="VN",
                import_date="2021-06-01",
            )
        )
        ledger.apply(
            warehouse_entry(
                ts="2024-01-02T00:00:00Z",
                entry_number="FAB-IN",
                hts="6006.22.1000",
                unit="kg",
                quantity=50,
                entered_value_usd="1800",
                country_of_origin="IN",
                import_date="2023-01-01",
            )
        )
        ledger.apply(
            {
                "type": "manufacture",
                "ts": "2024-02-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "output_hts": "6109.10.0012",
                "output_quantity": 80,
                "output_unit": "doz",
                "ref": "CUT-1",
                "bom": [
                    {"hts": "6006.22.1000", "country_of_origin": "VN", "quantity": 50},
                    {"hts": "6006.22.1000", "country_of_origin": "IN", "quantity": 40},
                ],
            }
        )
        inv = {row["lot_id"]: row for row in ledger.inventory("2024-02-01")}
        out = inv["MFG:CUT-1"]
        assert out["country_of_origin"] == "MIXED"
        assert out["import_date"] == "2021-06-01"
        assert out["quantity"] == "80"
        assert out["status"] == "bonded"
        assert "FAB-VN:1" not in inv
        assert inv["FAB-IN:1"]["quantity"] == "10"
        aging = {row["lot_id"]: row for row in ledger.aging("2024-02-01")}
        assert aging["MFG:CUT-1"]["last_legal_day"] == "2026-06-01"

    def test_empty_bom_rejected(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(hts="6006.22.1000", unit="kg", quantity=10, entered_value_usd="10", country_of_origin="VN"))
        with pytest.raises(InvalidBom):
            ledger.apply(
                {
                    "type": "manufacture",
                    "ts": "2024-02-01T00:00:00Z",
                    "warehouse_id": "EWR-BOND-4",
                    "output_hts": "6109.10.0012",
                    "output_quantity": 1,
                    "output_unit": "doz",
                    "ref": "CUT-EMPTY",
                    "bom": [],
                }
            )

    def test_output_duty_uses_output_hts_and_input_value(self):
        ledger = Ledger(HTS)
        ledger.apply(
            warehouse_entry(
                hts="6006.22.1000",
                unit="kg",
                quantity=10,
                entered_value_usd="1000",
                country_of_origin="VN",
            )
        )
        ledger.apply(
            {
                "type": "manufacture",
                "ts": "2024-02-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "output_hts": "6109.10.0012",
                "output_quantity": 4,
                "output_unit": "doz",
                "ref": "CUT-D",
                "bom": [{"hts": "6006.22.1000", "country_of_origin": "VN", "quantity": 10}],
            }
        )
        # output entered value = 1000; rate of shirts 0.165 -> 165.00
        assert ledger.duty_liability("2024-02-01")["total_usd"] == "165.00"


class TestRewarehouse:
    def test_clock_does_not_reset(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(import_date="2020-01-10", quantity=8, entered_value_usd="80"))
        ledger.apply(
            {
                "type": "rewarehouse",
                "ts": "2024-01-01T00:00:00Z",
                "from_warehouse_id": "EWR-BOND-4",
                "to_warehouse_id": "JFK-BOND-1",
                "quantity": 5,
                "lot_id": "E-1:1",
                "ref": "T22-1",
            }
        )
        inv = {row["lot_id"]: row for row in ledger.inventory("2024-01-01")}
        assert inv["E-1:1"]["warehouse_id"] == "EWR-BOND-4"
        assert inv["E-1:1"]["quantity"] == "3"
        assert inv["RW:T22-1"]["warehouse_id"] == "JFK-BOND-1"
        assert inv["RW:T22-1"]["quantity"] == "5"
        assert inv["RW:T22-1"]["import_date"] == "2020-01-10"
        aging = {row["lot_id"]: row for row in ledger.aging("2024-01-01")}
        assert aging["RW:T22-1"]["last_legal_day"] == "2025-01-10"

    def test_cannot_span_lots(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(entry_number="A", quantity=3, entered_value_usd="30"))
        ledger.apply(
            warehouse_entry(
                ts="2024-01-02T00:00:00Z",
                entry_number="B",
                quantity=3,
                entered_value_usd="30",
                import_date="2024-01-02",
            )
        )
        with pytest.raises(InsufficientQuantity):
            ledger.apply(
                {
                    "type": "rewarehouse",
                    "ts": "2024-02-01T00:00:00Z",
                    "from_warehouse_id": "EWR-BOND-4",
                    "to_warehouse_id": "JFK-BOND-1",
                    "hts": "6109.10.0012",
                    "country_of_origin": "VN",
                    "quantity": 5,
                    "ref": "T22-SPAN",
                }
            )


class TestAsOfAndInvariants:
    def test_as_of_datetime_cutoff(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(ts="2024-01-01T10:00:00Z", quantity=1, entered_value_usd="10"))
        ledger.apply(
            warehouse_entry(
                ts="2024-01-01T12:00:00Z",
                entry_number="E-2",
                quantity=1,
                entered_value_usd="10",
            )
        )
        ids = {row["lot_id"] for row in ledger.inventory("2024-01-01T11:00:00Z")}
        assert ids == {"E-1:1"}
        ids = {row["lot_id"] for row in ledger.inventory("2024-01-01")}
        assert ids == {"E-1:1", "E-2:1"}

    def test_quantity_conservation_fuzzer(self):
        rng = random.Random(20260817)
        ledger = Ledger(HTS)
        remaining = 0
        for i in range(8):
            qty = rng.randint(5, 20)
            ledger.apply(
                warehouse_entry(
                    ts=f"2024-01-{i+1:02d}T00:00:00Z",
                    entry_number=f"F-{i}",
                    quantity=qty,
                    entered_value_usd=str(qty * 10),
                    import_date=f"2024-01-{i+1:02d}",
                )
            )
            remaining += qty
        destroy = 15
        ledger.apply(
            {
                "type": "withdrawal",
                "ts": "2024-06-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": destroy,
                "ref": "WD-FUZZ",
            }
        )
        remaining -= destroy
        on_hand = sum(D(row["quantity"]) for row in ledger.inventory("2024-06-01") if row["status"] == "bonded")
        assert on_hand == D(remaining)

    def test_failed_event_does_not_stick(self):
        ledger = Ledger(HTS)
        ledger.apply(warehouse_entry(quantity=2, entered_value_usd="20"))
        with pytest.raises(UnknownHts):
            ledger.apply(
                warehouse_entry(
                    ts="2024-01-02T00:00:00Z",
                    entry_number="BAD",
                    hts="1111.11.1111",
                )
            )
        assert [row["lot_id"] for row in ledger.inventory("2024-12-01")] == ["E-1:1"]


class TestCliParity:
    def test_replay_writes_golden_reports(self, tmp_path):
        events = [
            warehouse_entry(),
            {
                "type": "withdrawal",
                "ts": "2024-01-10T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "consumption",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 100,
                "ref": "WD-CLI",
            },
        ]
        evpath = tmp_path / "events.json"
        evpath.write_text(json.dumps(events), encoding="utf-8")
        out = tmp_path / "out"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "bonded",
                "replay",
                str(evpath),
                "--hts",
                str(HTS_PATH),
                "--as-of",
                "2024-01-10",
                "--out",
                str(out),
            ],
            cwd=str(APP),
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        ledger = Ledger(HTS)
        ledger.apply_many(events)
        api = {
            "inventory": ledger.inventory("2024-01-10"),
            "duty_liability": ledger.duty_liability("2024-01-10"),
            "drawback_available": ledger.drawback_available("2024-01-10"),
            "aging": ledger.aging("2024-01-10"),
            "claims": ledger.claims("2024-01-10"),
        }
        for name, expected in api.items():
            got = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
            assert got == expected
        duty = api["duty_liability"]
        assert duty["total_usd"] == "3712.50"
        inv = {row["lot_id"]: row for row in api["inventory"]}
        assert inv["E-1:1"]["quantity"] == "900"
        assert inv["DP:WD-CLI:E-1:1"]["status"] == "duty_paid"
        assert inv["E-1:1"]["days_to_expiry"] == (date(2029, 1, 1) - date(2024, 1, 10)).days
        assert api["drawback_available"]["lines"] == []
        assert api["claims"] == []

    def test_bad_path_exits_nonzero(self, tmp_path):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "bonded",
                "replay",
                str(tmp_path / "missing.json"),
                "--hts",
                str(HTS_PATH),
                "--as-of",
                "2024-01-01",
                "--out",
                str(tmp_path / "out"),
            ],
            cwd=str(APP),
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
