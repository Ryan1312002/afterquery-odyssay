#!/usr/bin/env python3
"""Weighted grader. Always writes /logs/verifier/reward.txt."""

from __future__ import annotations

import json
import os
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path


CANARY = "odyssey-bonded-lot-drawback-engine-canary-7f3c1e"

WEIGHTS = {
    "TestSurface": 0.08,
    "TestFifo": 0.12,
    "TestDutyRounding": 0.10,
    "TestClock": 0.12,
    "TestDrawbackUnused": 0.10,
    "TestSubstitution": 0.10,
    "TestManufacture": 0.14,
    "TestRewarehouse": 0.08,
    "TestAsOfAndInvariants": 0.10,
    "TestCliParity": 0.06,
}


def write_reward(value: float, extra: dict | None = None) -> None:
    logs = Path(os.environ.get("BONDED_REWARD_DIR", "/logs/verifier"))
    logs.mkdir(parents=True, exist_ok=True)
    clipped = max(0.0, min(1.0, float(value)))
    (logs / "reward.txt").write_text(f"{clipped:.4f}\n", encoding="utf-8")
    payload = {"reward": clipped, **(extra or {})}
    (logs / "reward.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def agent_smuggled_canary() -> bool:
    root = Path(os.environ.get("BONDED_APP", "/app"))
    if not root.exists():
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "tests/public" in str(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if CANARY in text:
            return True
    return False


def class_name(testcase: ET.Element) -> str:
    raw = testcase.get("classname") or ""
    return raw.rsplit(".", 1)[-1]


def main() -> int:
    if agent_smuggled_canary():
        write_reward(0.0, {"reason": "canary leaked into /app"})
        return 0

    junit = Path("/tmp/bonded-junit.xml")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(os.environ.get("BONDED_APP", "/app"))) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "/tests/test_bonded.py" if Path("/tests/test_bonded.py").exists() else str(Path(__file__).parent / "test_bonded.py"),
            "-q",
            "--tb=short",
            f"--junitxml={junit}",
        ],
        env=env,
        cwd=str(Path(os.environ.get("BONDED_APP", "/app"))),
    )

    if not junit.exists():
        write_reward(0.0, {"reason": "pytest produced no junit report", "pytest_code": proc.returncode})
        return 0

    tree = ET.parse(junit)
    by_class: dict[str, list[bool]] = {name: [] for name in WEIGHTS}
    for case in tree.iter("testcase"):
        name = class_name(case)
        if name not in by_class:
            continue
        failed = case.find("failure") is not None or case.find("error") is not None
        by_class[name].append(not failed)

    channels = {}
    reward = 0.0
    for name, weight in WEIGHTS.items():
        results = by_class.get(name) or []
        if not results:
            frac = 0.0
        else:
            frac = sum(1 for ok in results if ok) / len(results)
        channels[name] = {"passed": round(frac, 4), "weight": weight, "n": len(results)}
        reward += weight * frac

    write_reward(reward, {"channels": channels, "pytest_code": proc.returncode})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        write_reward(0.0, {"reason": f"grader crashed: {exc}"})
        raise SystemExit(0)
