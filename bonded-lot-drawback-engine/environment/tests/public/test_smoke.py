from __future__ import annotations

from pathlib import Path
from decimal import Decimal

from bonded.hts import HtsTable

ROOT = Path(__file__).resolve().parents[2]
HTS = HtsTable.from_path(ROOT / "data" / "hts_duty.json")


def test_canonical_hts():
    assert HTS.canonical("6109100012") == "6109.10.0012"
    assert HTS.heading8("6109.10.0027") == "61091000"
    assert HTS.rate("6109.10.0012") == Decimal("0.165")


def test_fifo_takes_older_import_first():
    from bonded import Ledger

    ledger = Ledger(HTS)
    ledger.apply_many(
        [
            {
                "type": "warehouse_entry",
                "ts": "2024-01-10T00:00:00Z",
                "entry_number": "E-OLD",
                "line_number": 1,
                "warehouse_id": "EWR-BOND-4",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 10,
                "unit": "doz",
                "entered_value_usd": "1000",
                "import_date": "2024-01-01",
            },
            {
                "type": "warehouse_entry",
                "ts": "2024-01-11T00:00:00Z",
                "entry_number": "E-NEW",
                "line_number": 1,
                "warehouse_id": "EWR-BOND-4",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 10,
                "unit": "doz",
                "entered_value_usd": "1000",
                "import_date": "2024-06-01",
            },
            {
                "type": "withdrawal",
                "ts": "2024-07-01T00:00:00Z",
                "warehouse_id": "EWR-BOND-4",
                "purpose": "destruction",
                "hts": "6109.10.0012",
                "country_of_origin": "VN",
                "quantity": 10,
                "ref": "WD-FIFO",
            },
        ]
    )
    inv = {row["lot_id"]: row for row in ledger.inventory("2024-07-01")}
    assert "E-OLD:1" not in inv
    assert inv["E-NEW:1"]["quantity"] == "10"


def test_bankers_rounding_on_duty_line():
    from bonded import Ledger

    ledger = Ledger(HTS)
    ledger.apply(
        {
            "type": "warehouse_entry",
            "ts": "2024-03-01T00:00:00Z",
            "entry_number": "E-RND",
            "line_number": 1,
            "warehouse_id": "EWR-BOND-4",
            "hts": "6109.10.0012",
            "country_of_origin": "VN",
            "quantity": 1000,
            "unit": "doz",
            "entered_value_usd": "25000",
            "import_date": "2024-03-01",
        }
    )
    ledger.apply(
        {
            "type": "withdrawal",
            "ts": "2024-03-02T00:00:00Z",
            "warehouse_id": "EWR-BOND-4",
            "purpose": "consumption",
            "hts": "6109.10.0012",
            "country_of_origin": "VN",
            "quantity": 333,
            "ref": "WD-333",
        }
    )
    # 25000 * 333/1000 = 8325; 8325 * 0.165 = 1373.625 -> 1373.62 half-even
    liab = ledger.duty_liability("2024-03-02")
    remaining = Decimal("667")
    expected = (Decimal("25000") * remaining / Decimal("1000") * Decimal("0.165")).quantize(
        Decimal("0.01")
    )
    assert liab["lines"][0]["duty_usd"] == f"{expected:.2f}"
    dp = [r for r in ledger.inventory("2024-03-02") if r["status"] == "duty_paid"][0]
    assert dp["lot_id"] == "DP:WD-333:E-RND:1"


def test_drawback_is_ninety_nine_percent():
    from bonded import Ledger

    ledger = Ledger(HTS)
    ledger.apply(
        {
            "type": "consumption_entry",
            "ts": "2024-03-01T00:00:00Z",
            "entry_number": "E-01",
            "line_number": 1,
            "hts": "6109.10.0012",
            "country_of_origin": "VN",
            "quantity": 100,
            "unit": "doz",
            "entered_value_usd": "2500",
            "import_date": "2024-03-01",
        }
    )
    ledger.apply(
        {
            "type": "export_duty_paid",
            "ts": "2024-03-10T00:00:00Z",
            "hts": "6109.10.0012",
            "country_of_origin": "VN",
            "quantity": 100,
            "ref": "EXP-FULL",
        }
    )
    # duty = 2500 * 0.165 = 412.50; 99% = 408.375 -> 408.38
    avail = ledger.drawback_available("2024-03-10")
    assert avail["total_usd"] == "408.38"
    ledger.apply(
        {
            "type": "drawback_claim",
            "ts": "2024-03-11T00:00:00Z",
            "kind": "unused",
            "quantity": 100,
            "export_ref": "EXP-FULL",
            "claim_ref": "DBK-1",
        }
    )
    assert ledger.claims("2024-03-11")[0]["refund_usd"] == "408.38"


def test_as_of_excludes_later_journal_rows():
    from bonded import Ledger

    ledger = Ledger(HTS)
    ledger.apply(
        {
            "type": "warehouse_entry",
            "ts": "2024-01-01T00:00:00Z",
            "entry_number": "E-A",
            "line_number": 1,
            "warehouse_id": "EWR-BOND-4",
            "hts": "8504.40.9570",
            "country_of_origin": "CN",
            "quantity": 5,
            "unit": "ea",
            "entered_value_usd": "1000",
            "import_date": "2024-01-01",
        }
    )
    ledger.apply(
        {
            "type": "warehouse_entry",
            "ts": "2024-06-01T00:00:00Z",
            "entry_number": "E-B",
            "line_number": 1,
            "warehouse_id": "EWR-BOND-4",
            "hts": "8504.40.9570",
            "country_of_origin": "CN",
            "quantity": 7,
            "unit": "ea",
            "entered_value_usd": "1400",
            "import_date": "2024-06-01",
        }
    )
    ids = {row["lot_id"] for row in ledger.inventory("2024-03-15")}
    assert ids == {"E-A:1"}
