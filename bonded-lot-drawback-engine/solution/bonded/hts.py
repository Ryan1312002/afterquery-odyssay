from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from bonded.errors import UnknownHts


def normalize_hts(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) != 10:
        raise UnknownHts(f"HTS must be 10 digits, got {raw!r}")
    return digits


def canonical_hts(raw: str) -> str:
    d = normalize_hts(raw)
    return f"{d[0:4]}.{d[4:6]}.{d[6:10]}"


def heading8(raw: str) -> str:
    return normalize_hts(raw)[:8]


class HtsTable:
    def __init__(self, rows: dict[str, dict[str, Any]]):
        self._rows = rows

    @classmethod
    def from_path(cls, path: str | Path) -> "HtsTable":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise UnknownHts("HTS table must be a JSON object")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict) -> "HtsTable":
        rows: dict[str, dict[str, Any]] = {}
        for key, spec in data.items():
            digits = normalize_hts(key)
            if not isinstance(spec, dict) or "duty_ad_valorem" not in spec:
                raise UnknownHts(f"missing duty_ad_valorem for {key}")
            rows[digits] = {
                "duty_ad_valorem": Decimal(str(spec["duty_ad_valorem"])),
                "unit": spec.get("unit"),
                "description": spec.get("description"),
            }
        return cls(rows)

    def _row(self, hts: str) -> dict[str, Any]:
        digits = normalize_hts(hts)
        if digits not in self._rows:
            raise UnknownHts(hts)
        return self._rows[digits]

    def rate(self, hts: str) -> Decimal:
        return self._row(hts)["duty_ad_valorem"]

    def canonical(self, hts: str) -> str:
        self._row(hts)
        return canonical_hts(hts)

    def heading8(self, hts: str) -> str:
        self._row(hts)
        return heading8(hts)
