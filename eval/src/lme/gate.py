"""`uv run eval gate` -- T15. The gate that proves the gate.

TWO DIRECTIONS, both required:

  1. every SABOTEUR must FAIL (raise its expected GuardViolation). A saboteur
     that passes means the corresponding guard is dead.
  2. every POSITIVE CONTROL must PASS. Without this, a file full of
     `raise GuardViolation` unconditionally would score 15/15 and the gate would
     be measuring nothing.

Direction 2 is the one people skip, and it is exactly the failure mode of
`judge_canonical_lane.py --selftest`: it "passed" for weeks because it never
reached the live path. An instrument that has only ever returned zero has not
been shown to be able to return a positive.

The gate additionally checks that each saboteur trips THE TRAP IT CLAIMS TO --
`stale_run_id` raising a T7 would be a coincidence, not a working T1.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import EVAL_ROOT, guards

DEFAULT_FIXTURE_DIR: Path = EVAL_ROOT / "fixtures" / "negative"


def _load_fixture(fixture_dir: Path) -> Any:
    path = Path(fixture_dir) / "sabotaged.py"
    if not path.exists():
        raise FileNotFoundError(f"negative fixture missing: {path}")
    spec = importlib.util.spec_from_file_location("lme_negative_fixture", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lme_negative_fixture"] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class GateResult:
    saboteurs_total: int = 0
    saboteurs_caught: int = 0
    controls_total: int = 0
    controls_passed: int = 0
    failures: List[str] = field(default_factory=list)
    detail: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.failures
            and self.saboteurs_caught == self.saboteurs_total
            and self.controls_passed == self.controls_total
            and self.saboteurs_total > 0
            and self.controls_total > 0
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "saboteurs": f"{self.saboteurs_caught}/{self.saboteurs_total} caught",
            "positive_controls": f"{self.controls_passed}/{self.controls_total} passed",
            "failures": self.failures,
            "detail": self.detail,
        }


def run_gate(fixture_dir: Path = DEFAULT_FIXTURE_DIR, *, verbose: bool = True) -> GateResult:
    mod = _load_fixture(fixture_dir)
    res = GateResult()

    saboteurs = getattr(mod, "SABOTEURS", {})
    res.saboteurs_total = len(saboteurs)
    for name, (fn, expected_trap) in sorted(saboteurs.items()):
        try:
            fn()
        except guards.GuardViolation as exc:
            if exc.trap != expected_trap:
                res.failures.append(
                    f"SABOTEUR {name}: tripped {exc.trap}, expected {expected_trap} "
                    "-- the right guard is not the one firing"
                )
                res.detail.append(f"  ✗ {name:28s} wrong trap {exc.trap} != {expected_trap}")
            else:
                res.saboteurs_caught += 1
                res.detail.append(f"  ✓ {name:28s} correctly FAILED on {exc.trap}")
        except Exception as exc:  # noqa: BLE001
            res.failures.append(
                f"SABOTEUR {name}: raised {type(exc).__name__} instead of GuardViolation({expected_trap})"
            )
            res.detail.append(f"  ✗ {name:28s} {type(exc).__name__}: {exc}")
        else:
            res.failures.append(
                f"SABOTEUR {name}: PASSED THE GATE. Guard {expected_trap} is BROKEN "
                "-- every green this harness has emitted is now suspect."
            )
            res.detail.append(f"  ✗ {name:28s} PASSED (guard {expected_trap} is dead)")

    controls = getattr(mod, "POSITIVE_CONTROLS", {})
    res.controls_total = len(controls)
    for name, fn in sorted(controls.items()):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            res.failures.append(
                f"POSITIVE CONTROL {name}: raised {type(exc).__name__}: {exc}. "
                "The guards reject VALID input -- they are not discriminating, "
                "they are just always-on."
            )
            res.detail.append(f"  ✗ {name:28s} raised on clean input: {exc}")
        else:
            res.controls_passed += 1
            res.detail.append(f"  ✓ {name:28s} clean input accepted")

    if verbose:
        print("NEGATIVE FIXTURE (must FAIL):")
        for line in res.detail[: res.saboteurs_total]:
            print(line)
        print("POSITIVE CONTROLS (must PASS):")
        for line in res.detail[res.saboteurs_total :]:
            print(line)
        print()
        print(
            f"saboteurs caught {res.saboteurs_caught}/{res.saboteurs_total} · "
            f"controls passed {res.controls_passed}/{res.controls_total} · "
            f"{'GATE OK' if res.ok else 'GATE BROKEN'}"
        )
        for f in res.failures:
            print(f"  !! {f}")

    # T15 itself: assert the gate would have caught a sabotage that passed.
    guards.t15_negative_fixture(
        gate_result_on_sabotage=res.saboteurs_caught < res.saboteurs_total
    )
    return res


__all__ = ["DEFAULT_FIXTURE_DIR", "GateResult", "run_gate"]
