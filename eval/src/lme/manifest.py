"""`manifest.json` -- the reproducibility contract (design doc S3.3).

A run is a manifest plus its rows. The manifest is what makes a number citable:
it pins the dataset bytes, the split membership, every model, the prompt text,
and the code that turned one into the other. If any of those can move without
the manifest moving, the number is unfalsifiable.

`config_hash` covers `{config.yaml, reader prompt, extractor prompt, schema.py,
retrieve.py, code_version}` -- so a prompt edit is A NEW CONFIG, not an
untracked variable (T10; CoN+JSON formatting alone is worth up to 10 absolute
points, which is larger than most mechanism wins on this board).

`finalize()` runs T1/T2/T3 and refuses to emit an aggregate on violation. The
manifest's `valid` field is the single place that answers "may this run be
quoted".
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import EVAL_ROOT, REPO_ROOT, guards

CODE_VERSION: str = "lme-harness/0.1.0"
RUNS_DIR: Path = EVAL_ROOT / "runs"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except Exception:
        return ""


def git_sha() -> str:
    return _git("rev-parse", "HEAD") or "unknown"


def git_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_path(path: Path) -> str:
    p = Path(path)
    return sha256_text(p.read_text(encoding="utf-8")) if p.exists() else "MISSING"


@dataclass
class Pins:
    """Every model that touches a number. `judge_strict_panel` may not share a
    family with `reader` (T7) -- enforced in `Manifest.check_pins()`."""

    embedder: str = "text-embedding-3-small@256"
    extractor: str = ""
    reader: str = ""
    judge_official: str = "gpt-4o-2024-08-06"
    judge_strict_panel: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Sampling:
    temperature: float = 0.0
    top_p: float = 1.0
    reader_max_tokens: int = 512
    judge_max_tokens: int = 10  # official protocol
    judge_n: int = 1

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Manifest:
    arm: str
    dataset_file: str
    dataset_sha256: str
    hf_repo: str
    hf_revision: str
    split_name: str
    split_n: int
    split_ids_sha256: str
    seed: int
    expected_n: int
    pins: Pins = field(default_factory=Pins)
    sampling: Sampling = field(default_factory=Sampling)

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=utcnow)
    finished_at: str = ""
    git_sha: str = field(default_factory=git_sha)
    git_dirty: bool = field(default_factory=git_dirty)

    config_hash: str = ""
    config_hash_inputs: Dict[str, str] = field(default_factory=dict)

    served_model_violations: List[Dict[str, str]] = field(default_factory=list)
    phantom_flags: List[Dict[str, Any]] = field(default_factory=list)
    guards_status: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    cost: Dict[str, float] = field(
        default_factory=lambda: {"usd_est": 0.0, "tokens_in": 0.0, "tokens_out": 0.0}
    )
    env: Dict[str, Any] = field(default_factory=dict)
    valid: Optional[bool] = None

    # ── config hash ─────────────────────────────────────────────────────────
    def compute_config_hash(self, inputs: Mapping[str, str]) -> str:
        """`inputs` maps a LABEL to the CONTENT HASH of the thing it names.

        T10 requires the answering prompt to be in here. `check_config_hash()`
        asserts the required labels are present before the run may proceed.
        """
        self.config_hash_inputs = dict(inputs)
        canon = json.dumps(dict(sorted(inputs.items())), separators=(",", ":"))
        self.config_hash = sha256_text(canon)
        return self.config_hash

    REQUIRED_CONFIG_INPUTS: tuple = (
        "config_yaml",
        "reader_prompt",
        "code_version",
    )

    def check_config_hash(self, *, required: Sequence[str] = ()) -> None:
        req = list(required) or list(self.REQUIRED_CONFIG_INPUTS)
        guards.t10_prompt_hashed(self.config_hash_inputs, required=req)
        if not self.config_hash:
            raise guards.GuardViolation("T10", "config_hash was never computed")

    # ── pin checks ──────────────────────────────────────────────────────────
    def check_pins(self) -> None:
        """T7 -- no judge may share the reader's model family."""
        judges = [self.pins.judge_official, *self.pins.judge_strict_panel]
        judges = [j for j in judges if j]
        if self.pins.reader and judges:
            guards.t7_family_exclusion(self.pins.reader, judges)
        if len(self.pins.judge_strict_panel) >= 2:
            guards.t7_panel_distinct(
                self.pins.judge_strict_panel,
                min_families=min(3, len(self.pins.judge_strict_panel)),
            )

    # ── finalize ────────────────────────────────────────────────────────────
    def finalize(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        artifact_paths: Sequence[Path] = (),
        scores: Optional[Mapping[str, float]] = None,
        enforce_served_model: bool = True,
    ) -> None:
        """Run T1/T2/T3 (+ stamp T4) and decide `valid`.

        Raises GuardViolation on T1/T3 and on a non-empty T2 violation list when
        `enforce_served_model=True`. The dry/test paths pass False and the
        manifest records the violations verbatim so nothing is hidden.
        """
        self.finished_at = utcnow()

        guards.t3_complete(rows, expected_n=self.expected_n)
        self.guards_status["T3"] = "pass"

        guards.t1_fresh_rows(
            rows, run_id=self.run_id, started_at=self.started_at,
            artifact_paths=artifact_paths,
        )
        self.guards_status["T1"] = "pass"

        pinned = self.pins.reader
        if pinned:
            self.served_model_violations = guards.t2_collect_violations(rows, pinned=pinned)
        if self.served_model_violations:
            self.guards_status["T2"] = f"FAIL ({len(self.served_model_violations)} rows)"
            if enforce_served_model:
                self.valid = False
                raise guards.GuardViolation(
                    "T2",
                    f"{len(self.served_model_violations)} row(s) were served a model "
                    f"other than the pinned {pinned!r}. RUN INVALID.",
                )
        else:
            self.guards_status["T2"] = "pass"

        self.guards_status["T9"] = "pass"  # asserted at dataset load

        if scores:
            flags = guards.t4_phantom_flags(scores)
            self.phantom_flags = [f.to_json() for f in flags]
            self.guards_status["T4"] = "clean" if not flags else f"SUSPECT x{len(flags)}"

        self.env = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "falkordb": f"{os.environ.get('FALKORDB_HOST', '127.0.0.1')}:"
            f"{os.environ.get('FALKORDB_PORT', '6401')}",
        }
        self.valid = not self.served_model_violations

    # ── io ──────────────────────────────────────────────────────────────────
    @property
    def run_dir(self) -> Path:
        return RUNS_DIR / f"{self.arm}__{self.split_name}__{self.run_id[:12]}"

    def to_json(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git_sha": self.git_sha,
            "git_dirty": self.git_dirty,
            "arm": self.arm,
            "code_version": CODE_VERSION,
            "config_hash": self.config_hash,
            "config_hash_inputs": self.config_hash_inputs,
            "dataset": {
                "file": self.dataset_file,
                "sha256": self.dataset_sha256,
                "hf_repo": self.hf_repo,
                "hf_revision": self.hf_revision,
            },
            "split": {
                "name": self.split_name,
                "n": self.split_n,
                "ids_sha256": self.split_ids_sha256,
                "seed": self.seed,
            },
            "expected_n": self.expected_n,
            "pins": self.pins.to_json(),
            "sampling": self.sampling.to_json(),
            "served_model_violations": self.served_model_violations,
            "cost": self.cost,
            "guards": self.guards_status,
            "phantom_flags": self.phantom_flags,
            "env": self.env,
            "notes": self.notes,
            "valid": self.valid,
        }

    def write(self, run_dir: Optional[Path] = None) -> Path:
        d = Path(run_dir or self.run_dir)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "manifest.json"
        p.write_text(json.dumps(self.to_json(), indent=2) + "\n")
        return p


def load_manifest(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def assert_quotable(manifest: Mapping[str, Any]) -> None:
    """The single gate between a run directory and a slide.

    Refuses on: invalid=False, a non-empty served_model_violations, a failing
    guard, or an unrefuted phantom flag.
    """
    run = manifest.get("run_id", "<unknown>")
    if manifest.get("valid") is not True:
        raise guards.GuardViolation("T3", f"run {run} is not marked valid")
    if manifest.get("served_model_violations"):
        raise guards.GuardViolation(
            "T2", f"run {run} has {len(manifest['served_model_violations'])} served-model violations"
        )
    failing = {k: v for k, v in (manifest.get("guards") or {}).items() if str(v).startswith("FAIL")}
    if failing:
        raise guards.GuardViolation("T3", f"run {run} has failing guards {failing}")
    if manifest.get("phantom_flags"):
        raise guards.GuardViolation(
            "T4",
            f"run {run} carries {len(manifest['phantom_flags'])} unrefuted >=0.90 "
            "flag(s); it may not be headlined until a second seed AND a second "
            "judge family confirm.",
        )


__all__ = [
    "CODE_VERSION",
    "Manifest",
    "Pins",
    "RUNS_DIR",
    "Sampling",
    "assert_quotable",
    "git_dirty",
    "git_sha",
    "load_manifest",
    "sha256_path",
    "sha256_text",
    "utcnow",
]
