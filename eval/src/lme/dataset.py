"""Dataset load + pin + split resolve for LongMemEval.

THREE non-negotiables encoded here, each one a documented trap:

T9 (dataset drift)  -- sha256 is asserted at load against `eval/data/SHA256SUMS`.
    There are TWO files named `longmemeval_s*.json` in the wild, ~642 KB apart,
    and every published score sits on the CLEANED one. Refuse to run on mismatch.

T5 (oracle<->s mixing) -- the split id is part of every metric key. `split_tag()`
    returns e.g. `s_cleaned@98d7416`, and metrics.py folds it into the namespace
    so an oracle number and an `_s` number are structurally unmixable.

`datasets.load_dataset()` is NOT used -- the HF viewer/PyArrow type inference
fails because `answer` mixes `str` and `int` across the 500 rows (verified on
this box: 468 str / 32 int). Plain `json.load()`.

Two official behaviours replicated EXACTLY (see design doc S2.3):
  1. Abstention is a SUBSTRING CHECK ON THE ID: `'_abs' in question_id`.
     Those rows' `answer` field is an explanation of why the question is
     unanswerable, NOT a gold answer.
  2. Abstention rows are ALSO bucketed into their base `question_type`. The
     official `temporal-reasoning` accuracy therefore CONTAINS 6 abstention
     items and `Task-averaged Accuracy` is a macro over 6 CONTAMINATED buckets.
     We replicate the contamination for comparability and emit a clean 7-way
     table alongside, labelled `non-official`.

GOLD LEAK WARNING: turns carry `has_answer: true` on evidence turns. That is
GOLD. `Turn.text` and `Turn.role` are the only fields any arm may read at
inference time; `strip_gold()` below produces the inference-safe view and every
arm goes through it.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from . import EVAL_ROOT

DATA_DIR: Path = EVAL_ROOT / "data"
SPLITS_DIR: Path = EVAL_ROOT / "splits"
SHA256SUMS: Path = DATA_DIR / "SHA256SUMS"

#: xiaowu0162/longmemeval-cleaned @ main, 2025-09-19. Pinned in the manifest.
HF_REPO: str = "xiaowu0162/longmemeval-cleaned"
HF_REVISION: str = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
HF_REVISION_SHORT: str = HF_REVISION[:7]

#: The six official question types, in the order the paper reports them.
QUESTION_TYPES: Tuple[str, ...] = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "knowledge-update",
    "temporal-reasoning",
)

#: Machine-verified against longmemeval_oracle.json (n=500) -- design doc S2.3.
EXPECTED_TYPE_COUNTS: Dict[str, int] = {
    "temporal-reasoning": 133,
    "multi-session": 133,
    "knowledge-update": 78,
    "single-session-user": 70,
    "single-session-assistant": 56,
    "single-session-preference": 30,
}
EXPECTED_TOTAL: int = 500
EXPECTED_ABSTENTION: int = 30

#: The abstention marker. Official code: `abstention='_abs' in entry['question_id']`.
ABSTENTION_MARKER: str = "_abs"


class DatasetPinError(RuntimeError):
    """sha256 mismatch or missing pin -- T9. Never downgrade this to a warning."""


class SplitLockError(RuntimeError):
    """A named split's id-list hash does not match the registry."""


# ── hashing ─────────────────────────────────────────────────────────────────
def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Streaming sha256 -- the `_s_cleaned` file is 277 MB."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_ids(ids: Sequence[str]) -> str:
    """Split-membership hash. ORDER-SENSITIVE on purpose: a reordered split is a
    different split for any seeded/streaming consumer, so we lock the sequence."""
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def load_sha256sums(path: Path = SHA256SUMS) -> Dict[str, str]:
    """Parse the committed `shasum -a 256` file: `<hex>  <basename>`."""
    if not path.exists():
        raise DatasetPinError(
            f"{path} is missing. The dataset pin is the T9 guard; without it the "
            "harness cannot tell the cleaned release from the deprecated bytes."
        )
    sums: Dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        if not name:
            digest, _, name = line.partition(" ")
        sums[name.strip().lstrip("*")] = digest.strip()
    return sums


def assert_dataset_pin(path: Path) -> str:
    """T9: refuse to run on a dataset whose bytes are not the pinned release."""
    path = Path(path)
    if not path.exists():
        raise DatasetPinError(
            f"{path} does not exist. Run `make fetch-data` (see eval/README.md); "
            "the data files are gitignored, only their hashes are committed."
        )
    expected = load_sha256sums().get(path.name)
    if expected is None:
        raise DatasetPinError(
            f"{path.name} has no line in {SHA256SUMS}. Unpinned data may not be "
            "loaded -- that is exactly how a deprecated `_s` produced "
            "non-comparable numbers."
        )
    actual = sha256_file(path)
    if actual != expected:
        raise DatasetPinError(
            f"DATASET DRIFT (T9) on {path.name}\n"
            f"  expected {expected}\n"
            f"  actual   {actual}\n"
            f"Refusing to run. The pinned release is {HF_REPO}@{HF_REVISION}."
        )
    return actual


# ── records ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Turn:
    role: str
    text: str
    ordinal: int
    #: GOLD. Present for bookkeeping/recall diagnostics ONLY. Never serve it.
    has_answer: bool = False


@dataclass(frozen=True)
class Session:
    session_id: str
    date_raw: str
    date_iso: str
    #: position in DATE order (not file order). oracle haystacks are UNSORTED.
    ordinal: int
    turns: List[Turn] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(f"{t.role}: {t.text}" for t in self.turns)


@dataclass(frozen=True)
class Question:
    question_id: str
    question_type: str
    question: str
    #: str for 468 rows, int for 32 -- kept as-loaded, stringified only at judge time.
    answer: Any
    question_date: str
    sessions: List[Session]
    gold_session_ids: List[str]

    @property
    def is_abstention(self) -> bool:
        """Official rule, verbatim: a substring check on the id."""
        return ABSTENTION_MARKER in self.question_id

    @property
    def official_bucket(self) -> str:
        """The bucket official code reports this row under.

        Abstention rows are ALSO bucketed into their base question_type -- the
        official `temporal-reasoning` number contains 6 abstention items. This
        property replicates that contamination.
        """
        return self.question_type

    @property
    def clean_bucket(self) -> str:
        """The `non-official` clean 7-way bucket: abstention gets its own."""
        return "abstention" if self.is_abstention else self.question_type

    @property
    def gold_str(self) -> str:
        return str(self.answer)


def parse_date_iso(raw: str) -> str:
    """`'2023/04/10 (Mon) 17:50'` -> `'2023-04-10T17:50'` (lexically sortable).

    Returns `''` for anything unparseable rather than raising: a missing date
    must degrade the temporal spine, not kill the run. Callers that depend on
    the date (the [:NEXT] spine, S4) check for the empty string explicitly.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    head = raw.split(" ")[0]
    parts = head.split("/")
    if len(parts) != 3:
        return ""
    y, m, d = parts
    if not (y.isdigit() and m.isdigit() and d.isdigit()):
        return ""
    time_part = ""
    tokens = raw.split(" ")
    for tok in tokens[1:]:
        if ":" in tok:
            time_part = tok
            break
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}" + (f"T{time_part}" if time_part else "")


def _build_question(entry: Dict[str, Any]) -> Question:
    dates = entry.get("haystack_dates") or []
    sids = entry.get("haystack_session_ids") or []
    sessions_raw = entry.get("haystack_sessions") or []
    if not (len(dates) == len(sids) == len(sessions_raw)):
        raise ValueError(
            f"{entry['question_id']}: haystack arity mismatch "
            f"dates={len(dates)} ids={len(sids)} sessions={len(sessions_raw)}"
        )

    staged: List[Tuple[str, str, str, List[Turn]]] = []
    for sid, draw, turns_raw in zip(sids, dates, sessions_raw):
        turns = [
            Turn(
                role=str(t.get("role", "user")),
                text=str(t.get("content", "")),
                ordinal=i,
                has_answer=bool(t.get("has_answer", False)),
            )
            for i, t in enumerate(turns_raw)
        ]
        staged.append((sid, draw, parse_date_iso(draw), turns))

    # SORT BY DATE. The `_s` haystack ships timestamp-sorted; the ORACLE haystack
    # does NOT (verified: oracle q0 dates are 17:50, 14:47, 17:15). Assuming file
    # order is chronological is a real trap -- so ordinal is always date-derived.
    order = sorted(range(len(staged)), key=lambda i: (staged[i][2] == "", staged[i][2], staged[i][0]))
    sessions = [
        Session(
            session_id=staged[i][0],
            date_raw=staged[i][1],
            date_iso=staged[i][2],
            ordinal=rank,
            turns=staged[i][3],
        )
        for rank, i in enumerate(order)
    ]

    return Question(
        question_id=entry["question_id"],
        question_type=entry["question_type"],
        question=entry["question"],
        answer=entry["answer"],
        question_date=entry.get("question_date", ""),
        sessions=sessions,
        gold_session_ids=list(entry.get("answer_session_ids") or []),
    )


# ── loading ─────────────────────────────────────────────────────────────────
@dataclass
class Dataset:
    path: Path
    sha256: str
    questions: List[Question]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def split_id(self) -> str:
        """T5 metric-namespace tag: `oracle@98d7416` / `s_cleaned@98d7416`."""
        stem = self.path.stem.replace("longmemeval_", "")
        return f"{stem}@{HF_REVISION_SHORT}"

    def by_id(self) -> Dict[str, Question]:
        return {q.question_id: q for q in self.questions}

    def subset(self, ids: Sequence[str]) -> List[Question]:
        index = self.by_id()
        missing = [i for i in ids if i not in index]
        if missing:
            raise KeyError(
                f"{len(missing)} split id(s) absent from {self.name}, "
                f"first few: {missing[:5]}. A split built on one file cannot be "
                f"resolved against another (T5)."
            )
        return [index[i] for i in ids]

    def __iter__(self) -> Iterator[Question]:
        return iter(self.questions)

    def __len__(self) -> int:
        return len(self.questions)


def load_dataset(path: Path | str, *, verify: bool = True) -> Dataset:
    """Load + pin-assert. `verify=False` is TEST-ONLY (fixtures, unit tests)."""
    path = Path(path)
    digest = assert_dataset_pin(path) if verify else sha256_file(path)
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)  # NOT datasets.load_dataset -- see module docstring
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: expected a top-level JSON list, got {type(raw).__name__}")
    return Dataset(path=path, sha256=digest, questions=[_build_question(e) for e in raw])


def strip_gold(session: Session) -> List[Dict[str, str]]:
    """The inference-safe view of a session: role + text ONLY.

    `has_answer` is gold and never crosses this boundary. Every arm builds its
    context from this function so the leak is impossible by construction rather
    than by discipline.
    """
    return [{"role": t.role, "text": t.text} for t in session.turns]


# ── splits ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Split:
    name: str
    dataset_file: str
    seed: int
    ids: List[str]
    ids_sha256: str
    strategy: str

    @property
    def n(self) -> int:
        return len(self.ids)

    def to_json(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dataset_file": self.dataset_file,
            "hf_repo": HF_REPO,
            "hf_revision": HF_REVISION,
            "seed": self.seed,
            "strategy": self.strategy,
            "n": self.n,
            "ids_sha256": self.ids_sha256,
            "ids": self.ids,
        }


def stratified_ids(
    questions: Sequence[Question],
    *,
    per_type: Optional[int] = None,
    total: Optional[int] = None,
    top_up_to: Optional[int] = None,
    seed: int = 20260803,
) -> List[str]:
    """Stratified sample over `question_type`.

    `per_type` -> exactly k per type (smoke-30). `total` -> proportional
    allocation with a largest-remainder pass (dev-150). Deterministic given the
    seed: buckets are sorted by id before sampling so the file order of the
    source JSON cannot change the outcome.

    `top_up_to` (only with `per_type`) -> a FLOOR of k per type, then the
    remaining `top_up_to - k*len(QUESTION_TYPES)` rows go to the LARGEST buckets
    by dataset population, ties broken by type name. This exists because a smoke
    size need not divide evenly by the 6 types (smoke-40: floor 6, +4). It keeps
    the smoke idiom -- every type gets a workable floor so every code path is
    exercised -- rather than the proportional idiom, which would starve
    `single-session-preference` (n=30 -> 2 rows at n=40) and leave the
    supersede/temporal mechanisms under-sampled.

    With `top_up_to=None` the allocation is `per_type` for every bucket, so the
    rng call sequence is byte-identical to the pre-`top_up_to` implementation
    and smoke-30 regenerates to its locked `ids_sha256`.
    """
    if (per_type is None) == (total is None):
        raise ValueError("pass exactly one of per_type= or total=")
    if top_up_to is not None and per_type is None:
        raise ValueError("top_up_to= requires per_type= (it is a floor, not a total)")

    buckets: Dict[str, List[str]] = {t: [] for t in QUESTION_TYPES}
    for q in questions:
        buckets.setdefault(q.question_type, []).append(q.question_id)
    for t in buckets:
        buckets[t].sort()

    rng = random.Random(seed)
    picked: List[str] = []

    if per_type is not None:
        alloc = {t: per_type for t in QUESTION_TYPES}
        if top_up_to is not None:
            extra = top_up_to - per_type * len(QUESTION_TYPES)
            if extra < 0:
                raise ValueError(
                    f"top_up_to={top_up_to} is below the floor "
                    f"{per_type}*{len(QUESTION_TYPES)}={per_type * len(QUESTION_TYPES)}"
                )
            if extra > len(QUESTION_TYPES):
                raise ValueError(
                    f"top_up_to={top_up_to} needs {extra} extra rows over "
                    f"{len(QUESTION_TYPES)} types; raise per_type instead so the "
                    "allocation stays one-extra-per-bucket and unambiguous"
                )
            # Largest buckets by POPULATION, ties by type name -> seed-independent
            # and stable under any reordering of the source JSON.
            for t in sorted(QUESTION_TYPES, key=lambda t: (-len(buckets.get(t, [])), t))[:extra]:
                alloc[t] += 1
        for t in QUESTION_TYPES:
            pool = buckets.get(t, [])
            if len(pool) < alloc[t]:
                raise ValueError(f"type {t!r} has {len(pool)} rows, need {alloc[t]}")
            picked.extend(rng.sample(pool, alloc[t]))
    else:
        n_all = sum(len(v) for v in buckets.values())
        assert total is not None
        exact = {t: len(buckets.get(t, [])) * total / n_all for t in QUESTION_TYPES}
        alloc = {t: int(exact[t]) for t in QUESTION_TYPES}
        # largest-remainder, ties broken by type name so it is seed-independent
        short = total - sum(alloc.values())
        for t in sorted(QUESTION_TYPES, key=lambda t: (-(exact[t] - alloc[t]), t))[:short]:
            alloc[t] += 1
        for t in QUESTION_TYPES:
            pool = buckets.get(t, [])
            k = min(alloc[t], len(pool))
            picked.extend(rng.sample(pool, k))

    picked.sort()  # canonical order -> a stable ids_sha256
    return picked


def make_split(
    name: str,
    dataset: Dataset,
    *,
    per_type: Optional[int] = None,
    total: Optional[int] = None,
    top_up_to: Optional[int] = None,
    all_rows: bool = False,
    seed: int = 20260803,
) -> Split:
    if all_rows:
        ids = sorted(q.question_id for q in dataset.questions)
        strategy = "all"
    elif per_type is not None:
        ids = stratified_ids(
            dataset.questions, per_type=per_type, top_up_to=top_up_to, seed=seed
        )
        if top_up_to is None:
            strategy = f"stratified/{per_type}-per-type"
        else:
            extra = top_up_to - per_type * len(QUESTION_TYPES)
            strategy = f"stratified/{per_type}-per-type+{extra}-largest-buckets"
    else:
        ids = stratified_ids(dataset.questions, total=total, seed=seed)
        strategy = f"stratified-proportional/{total}"
    return Split(
        name=name,
        dataset_file=dataset.name,
        seed=seed,
        ids=ids,
        ids_sha256=sha256_ids(ids),
        strategy=strategy,
    )


def registry_path() -> Path:
    return SPLITS_DIR / "registry.json"


def load_registry() -> Dict[str, Any]:
    p = registry_path()
    return json.loads(p.read_text()) if p.exists() else {}


def write_split(split: Split) -> Path:
    SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    p = SPLITS_DIR / f"{split.name}.json"
    p.write_text(json.dumps(split.to_json(), indent=2) + "\n")
    reg = load_registry()
    reg[split.name] = {
        "dataset_file": split.dataset_file,
        "hf_revision": HF_REVISION,
        "seed": split.seed,
        "strategy": split.strategy,
        "n": split.n,
        "ids_sha256": split.ids_sha256,
    }
    registry_path().write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")
    return p


def load_split(name: str) -> Split:
    """Load a named split and CHECK IT AGAINST THE REGISTRY.

    The registry lock is the one discipline worth keeping from prior art: a
    split file edited by hand (or regenerated under a different seed) is a
    different experiment wearing the same name.
    """
    p = SPLITS_DIR / f"{name}.json"
    if not p.exists():
        raise SplitLockError(f"unknown split {name!r} -- no {p}. Run `uv run eval splits`.")
    body = json.loads(p.read_text())
    ids = list(body["ids"])
    actual = sha256_ids(ids)
    if actual != body.get("ids_sha256"):
        raise SplitLockError(
            f"split {name!r} self-hash mismatch: file says {body.get('ids_sha256')}, "
            f"ids hash to {actual}. The split file was edited."
        )
    reg = load_registry().get(name)
    if reg is None:
        raise SplitLockError(f"split {name!r} is not in {registry_path()}.")
    if reg["ids_sha256"] != actual:
        raise SplitLockError(
            f"SPLIT LOCK BROKEN for {name!r}\n"
            f"  registry {reg['ids_sha256']}\n"
            f"  file     {actual}\n"
            "Refusing to run a named split whose membership changed."
        )
    if reg["n"] != len(ids):
        raise SplitLockError(f"split {name!r}: registry n={reg['n']} but file has {len(ids)}")
    return Split(
        name=name,
        dataset_file=body["dataset_file"],
        seed=int(body["seed"]),
        ids=ids,
        ids_sha256=actual,
        strategy=body.get("strategy", "unknown"),
    )


def resolve(dataset: Dataset, split_name: str) -> Tuple[Split, List[Question]]:
    """Load a split, verify it was built on THIS dataset file (T5), resolve rows."""
    split = load_split(split_name)
    if split.dataset_file != dataset.name:
        raise SplitLockError(
            f"SPLIT/DATASET MISMATCH (T5): split {split_name!r} was built on "
            f"{split.dataset_file}, you passed {dataset.name}. Oracle numbers and "
            f"_s numbers are not comparable and the harness will not mix them."
        )
    return split, dataset.subset(split.ids)


__all__ = [
    "ABSTENTION_MARKER",
    "Dataset",
    "DatasetPinError",
    "EXPECTED_ABSTENTION",
    "EXPECTED_TOTAL",
    "EXPECTED_TYPE_COUNTS",
    "HF_REPO",
    "HF_REVISION",
    "HF_REVISION_SHORT",
    "QUESTION_TYPES",
    "Question",
    "Session",
    "Split",
    "SplitLockError",
    "Turn",
    "assert_dataset_pin",
    "load_dataset",
    "load_registry",
    "load_split",
    "make_split",
    "parse_date_iso",
    "resolve",
    "sha256_file",
    "sha256_ids",
    "strip_gold",
    "write_split",
]
