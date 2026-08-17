from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bonded.hts import HtsTable
from bonded.ledger import Ledger


def _load_events(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return payload["events"]
    raise SystemExit("event file must be a list or an object with an events array")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bonded")
    sub = parser.add_subparsers(dest="cmd", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("events")
    replay.add_argument("--hts", required=True)
    replay.add_argument("--as-of", required=True)
    replay.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        table = HtsTable.from_path(args.hts)
        ledger = Ledger(table)
        ledger.apply_many(_load_events(Path(args.events)))
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "inventory.json").write_text(
            json.dumps(ledger.inventory(args.as_of), indent=2) + "\n", encoding="utf-8"
        )
        (out / "duty_liability.json").write_text(
            json.dumps(ledger.duty_liability(args.as_of), indent=2) + "\n", encoding="utf-8"
        )
        (out / "drawback_available.json").write_text(
            json.dumps(ledger.drawback_available(args.as_of), indent=2) + "\n", encoding="utf-8"
        )
        (out / "aging.json").write_text(
            json.dumps(ledger.aging(args.as_of), indent=2) + "\n", encoding="utf-8"
        )
        (out / "claims.json").write_text(
            json.dumps(ledger.claims(args.as_of), indent=2) + "\n", encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
