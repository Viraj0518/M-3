"""`uv run eval <subcommand>`.

    splits    build/lock smoke-30, dev-150, full-500 + registry.json
    run       execute one or more arms over a split
    judge     score an existing run dir (official lane and/or strict panel)
    report    render eval/runs/BOARD.md from every valid run dir
    gate      T15 -- the sabotaged fixture must fail, the controls must pass
    verify    no-cost preflight: dataset pins, split locks, prompts, FalkorDB

`verify` exists because every expensive failure on this benchmark was a cheap
precondition nobody checked: a missing env var, a stale file, an unpinned
dataset. It costs nothing and takes seconds.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import EVAL_ROOT, dataset as ds, guards, metrics
from .gate import DEFAULT_FIXTURE_DIR, run_gate

DEFAULT_SEED = 20260803
ORACLE = EVAL_ROOT / "data" / "longmemeval_oracle.json"
S_CLEANED = EVAL_ROOT / "data" / "longmemeval_s_cleaned.json"


# ── splits ──────────────────────────────────────────────────────────────────
def cmd_splits(args: argparse.Namespace) -> int:
    made: List[str] = []
    if not args.only or args.only == "smoke-30":
        oracle = ds.load_dataset(ORACLE)
        _sanity_taxonomy(oracle)
        sp = ds.make_split("smoke-30", oracle, per_type=5, seed=args.seed)
        ds.write_split(sp)
        made.append(f"smoke-30      n={sp.n:3d}  {sp.dataset_file}  {sp.ids_sha256[:16]}…")
    if not args.only or args.only == "smoke-40":
        oracle = ds.load_dataset(ORACLE)
        _sanity_taxonomy(oracle)
        # 40 does not divide by the 6 types: floor 6 each (=36), then +1 to each
        # of the 4 largest buckets by population (multi-session, temporal-
        # reasoning, knowledge-update, single-session-user) -> 7/7/7/7/6/6.
        sp = ds.make_split("smoke-40", oracle, per_type=6, top_up_to=40, seed=args.seed)
        ds.write_split(sp)
        made.append(f"smoke-40      n={sp.n:3d}  {sp.dataset_file}  {sp.ids_sha256[:16]}…")
    if not args.only or args.only in ("dev-150", "full-500"):
        s = ds.load_dataset(S_CLEANED)
        _sanity_taxonomy(s)
        if not args.only or args.only == "dev-150":
            sp = ds.make_split("dev-150", s, total=150, seed=args.seed)
            ds.write_split(sp)
            made.append(f"dev-150       n={sp.n:3d}  {sp.dataset_file}  {sp.ids_sha256[:16]}…")
        if not args.only or args.only == "full-500":
            sp = ds.make_split("full-500", s, all_rows=True, seed=args.seed)
            ds.write_split(sp)
            made.append(f"full-500      n={sp.n:3d}  {sp.dataset_file}  {sp.ids_sha256[:16]}…")
    for line in made:
        print(line)
    print(f"registry: {ds.registry_path()}")
    return 0


def _sanity_taxonomy(d: ds.Dataset) -> None:
    """Positive check that the loader agrees with the design doc's machine-verified
    counts. If this drifts, the dataset changed under us and every prior number
    is on different data."""
    from collections import Counter

    counts = Counter(q.question_type for q in d.questions)
    if len(d) != ds.EXPECTED_TOTAL:
        raise guards.GuardViolation("T9", f"{d.name}: n={len(d)}, expected {ds.EXPECTED_TOTAL}")
    for t, n in ds.EXPECTED_TYPE_COUNTS.items():
        if counts.get(t, 0) != n:
            raise guards.GuardViolation(
                "T9", f"{d.name}: {t} n={counts.get(t,0)}, expected {n}"
            )
    n_abs = sum(1 for q in d.questions if q.is_abstention)
    if n_abs != ds.EXPECTED_ABSTENTION:
        raise guards.GuardViolation(
            "T9", f"{d.name}: {n_abs} `_abs` rows, expected {ds.EXPECTED_ABSTENTION}"
        )


# ── run ─────────────────────────────────────────────────────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_arm

    arms: List[str] = list(args.arm or [])
    if args.all_arms:
        arms = ["a0_fullcontext", "a1_naive_rag", "a3_palimpsest"]  # A2 is NOT default
    if args.ablations:
        arms += [f"a3_ablate_{m}" for m in ("supersede", "temporal", "expansion", "sufficiency")]
    if not arms:
        print("no arms selected: pass --arm NAME, --all-arms, or --ablations", file=sys.stderr)
        return 2

    data = Path(args.data) if args.data else ORACLE
    results: List[Dict[str, Any]] = []
    for arm in arms:
        # --dry OVERLAYS the stubs onto the arm's own config, it does not
        # replace it -- so `--dry --ablations` still exercises each removal test.
        cfg = args.config or arm
        label = f"{cfg}+dry" if args.dry else cfg
        print(f"\n=== {arm}  (config={label}, split={args.split}, data={data.name}) ===")
        try:
            r = run_arm(
                arm_name=arm,
                config_name=cfg,
                data_path=data,
                split_name=args.split,
                seed=args.seed,
                limit=args.limit,
                progress=not args.quiet,
                dry=args.dry,
            )
        except guards.GuardViolation as exc:
            print(f"GUARD FAILED: {exc}", file=sys.stderr)
            return 1
        results.append(r)
        rec = {k: v for k, v in r["retrieval"].items() if "recall@" in k}
        print(f"  run_id={r['run_id'][:12]}  n={r['n']}  valid={r['valid']}  dry={r['dry']}")
        for k, v in sorted(rec.items()):
            print(f"    {k} = {v:.4f}")
        print(f"  artifacts: {r['run_dir']}")

    # T12: arms compared head-to-head must have served the same budget.
    print("\nsummary")
    for r in results:
        print(f"  {r['arm']:24s} n={r['n']:3d} valid={r['valid']} {r['run_dir']}")
    return 0


# ── judge ───────────────────────────────────────────────────────────────────
def cmd_judge(args: argparse.Namespace) -> int:
    from .judge_official import OfficialJudge
    from .manifest import load_manifest

    run_dir = Path(args.run)
    man = load_manifest(run_dir / "manifest.json")
    rows = [json.loads(l) for l in (run_dir / "rows.jsonl").read_text().splitlines() if l.strip()]

    guards.t3_complete(rows, expected_n=int(man["expected_n"]))
    guards.t1_fresh_rows(rows, run_id=man["run_id"], started_at=man["started_at"])
    if man.get("notes") and any("DRY RUN" in n for n in man["notes"]) and not args.force_dry:
        print(
            "REFUSING: this run used the dry stubs; its predictions are not model "
            "output and judging them would spend money to score noise. Pass "
            "--force-dry only if you really mean it.",
            file=sys.stderr,
        )
        return 2

    judge = OfficialJudge()
    verdicts = judge.judge_rows(rows)
    (run_dir / "judged.official.json").write_text(
        json.dumps([v.to_json() for v in verdicts], indent=2) + "\n"
    )

    jrows = [
        metrics.JudgedRow(
            question_id=v.question_id,
            question_type=v.question_type,
            is_abstention=v.is_abstention,
            label=v.label,
            predicted_abstain=bool(r.get("predicted_abstain")),
        )
        for v, r in zip(verdicts, rows)
    ]
    split_id = man["dataset"]["file"].replace("longmemeval_", "").replace(".json", "")
    split_id = f"{split_id}@{man['dataset']['hf_revision'][:7]}"
    qa = metrics.qa_metrics(jrows, split_id=split_id)

    agg_path = run_dir / "aggregate.json"
    agg = json.loads(agg_path.read_text()) if agg_path.exists() else {}
    agg.update(qa)
    agg["judged"] = True
    agg["judge_lane"] = "official"
    metrics.combine(qa)  # T6 + T8 enforced before anything is written
    agg_path.write_text(json.dumps(agg, indent=2, default=str) + "\n")

    flags = guards.t4_phantom_flags(metrics.scalar_scores(qa))
    for k, v in sorted(qa.items()):
        if isinstance(v, float):
            print(f"  {k} = {v:.4f}")
    for f in flags:
        print(f"  !! T4 {f.metric} = {f.value:.4f} -> {f.status}")
    return 0


# ── report ──────────────────────────────────────────────────────────────────
def cmd_report(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs)
    out = Path(args.out)
    rows: List[str] = [
        "# LongMemEval board — PALIMPSEST",
        "",
        "Generated by `uv run eval report`. Every number here is traceable to a",
        "`manifest.json` with a dataset sha256, a split ids_sha256, and a config_hash.",
        "",
        "Cells marked `SUSPECT(T4)` are >= 0.90 and are BLOCKED from being quoted",
        "until a second seed AND a second judge family confirm them. Prior work on",
        "this benchmark is 4-for-4 on >= 0.9 being a phantom.",
        "",
    ]
    arm_metrics: Dict[str, Dict[str, Any]] = {}
    budgets: Dict[str, int] = {}
    dry_runs: List[str] = []
    for d in sorted(runs_dir.glob("*/")) if runs_dir.exists() else []:
        mp, ap = d / "manifest.json", d / "aggregate.json"
        if not (mp.exists() and ap.exists()):
            continue
        man = json.loads(mp.read_text())
        agg = json.loads(ap.read_text())
        label = f"{man['arm']}@{man['split']['name']}"
        if any("DRY RUN" in n for n in man.get("notes", [])):
            dry_runs.append(f"{label} ({man['run_id'][:12]})")
            continue
        arm_metrics[label] = agg
        if isinstance(agg.get("budget"), int) and man["arm"] != "a0_fullcontext":
            budgets[label] = agg["budget"]

    if arm_metrics:
        rows += ["## Judged arms", "", "```"] + metrics.render_board(arm_metrics) + ["```", ""]
        try:
            guards.t12_compare_budgets(budgets)
            rows.append(f"Equal-k comparison verified: all non-A0 arms served k={sorted(set(budgets.values()))}.")
        except guards.GuardViolation as exc:
            rows.append(f"**T12 WARNING** — {exc}")
        rows.append("")
    else:
        rows += ["_No judged runs on the board yet._", ""]

    if dry_runs:
        rows += [
            "## Excluded: dry runs (plumbing only)",
            "",
            "These used `test_embedder`/`test_reader`/`test_extractor`. Their qa.* numbers",
            "are meaningless by construction and they are never rendered as results.",
            "",
        ] + [f"- {r}" for r in dry_runs] + [""]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n")
    print(f"wrote {out}  ({len(arm_metrics)} judged run(s), {len(dry_runs)} dry run(s) excluded)")
    return 0


# ── gate ────────────────────────────────────────────────────────────────────
def cmd_gate(args: argparse.Namespace) -> int:
    res = run_gate(Path(args.negative) if args.negative else DEFAULT_FIXTURE_DIR)
    return 0 if res.ok else 1


# ── verify ──────────────────────────────────────────────────────────────────
def cmd_verify(args: argparse.Namespace) -> int:
    """No-cost preflight. Every expensive failure here was a cheap precondition."""
    ok = True

    def check(label: str, fn: Any) -> None:
        nonlocal ok
        try:
            detail = fn()
            print(f"  PASS  {label}" + (f"  — {detail}" if detail else ""))
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  FAIL  {label}  — {type(exc).__name__}: {exc}")

    print("dataset pins (T9)")
    for p in (ORACLE, S_CLEANED):
        check(p.name, lambda p=p: ds.assert_dataset_pin(p)[:16] + "…")

    print("split locks")
    for name in ("smoke-30", "smoke-40", "dev-150", "full-500"):
        check(name, lambda n=name: f"n={ds.load_split(n).n}, {ds.load_split(n).ids_sha256[:16]}…")

    print("prompts")
    from .judge_strict import load_rubric
    from .reader import load_reader_prompt

    check("reader_v1.md", lambda: f"{len(load_reader_prompt()[1])} chars (user template)")
    check("strict_rubric_v1_1.md", lambda: f"{len(load_rubric())} chars")
    check(
        "official judge templates",
        lambda: _judge_template_pin(),
    )

    print("graph vocabulary")
    from .graph.schema import assert_directed_vocabulary

    check("memory.taxonomy directed relations", lambda: assert_directed_vocabulary() or "supersedes/contradicts directed")

    print("falkordb")
    check("GRAPH.QUERY on the configured port", _falkor_probe)

    print("keys (names only, never values)")
    import os

    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        print(f"  {'SET  ' if os.environ.get(k) else 'UNSET'} {k}")

    print("\n" + ("VERIFY OK" if ok else "VERIFY FAILED"))
    return 0 if ok else 1


def _judge_template_pin() -> str:
    from .judge_official import _probe_templates

    return _probe_templates()[:16] + "…"


def _falkor_probe() -> str:
    """POSITIVE probe. Connecting is not enough: a stray `redis-server` accepts
    the connection and then fails on GRAPH.QUERY. We assert a real result set."""
    from .graph.schema import FALKORDB_HOST, FALKORDB_PORT, GraphHandle

    g = GraphHandle("lme_verify_probe", host=FALKORDB_HOST, port=FALKORDB_PORT)
    try:
        res = g.query("RETURN 1 AS x")
        if not res.result_set or int(res.result_set[0][0]) != 1:
            raise RuntimeError("GRAPH.QUERY returned no usable result set")
        return f"{FALKORDB_HOST}:{FALKORDB_PORT} answered GRAPH.QUERY"
    finally:
        g.drop()


# ── argparse ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eval", description="PALIMPSEST × LongMemEval harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("splits", help="build + lock the frozen splits")
    sp.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sp.add_argument("--only", choices=["smoke-30", "smoke-40", "dev-150", "full-500"])
    sp.set_defaults(func=cmd_splits)

    r = sub.add_parser("run", help="run one or more arms")
    r.add_argument("--arm", action="append")
    r.add_argument("--all-arms", action="store_true", help="A0 + A1 + A3 (never A2)")
    r.add_argument("--ablations", action="store_true")
    r.add_argument("--config")
    r.add_argument("--data")
    r.add_argument("--split", default="smoke-30")
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--dry", action="store_true", help="use configs/dry_smoke.yaml (stubs, no keys, non-citable)")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=cmd_run)

    j = sub.add_parser("judge", help="official-lane judge over an existing run dir")
    j.add_argument("--run", required=True)
    j.add_argument("--force-dry", action="store_true")
    j.set_defaults(func=cmd_judge)

    rep = sub.add_parser("report", help="render the board")
    rep.add_argument("--runs", default=str(EVAL_ROOT / "runs"))
    rep.add_argument("--out", default=str(EVAL_ROOT / "runs" / "BOARD.md"))
    rep.set_defaults(func=cmd_report)

    g = sub.add_parser("gate", help="T15 negative fixture + positive controls")
    g.add_argument("--negative", default=str(DEFAULT_FIXTURE_DIR))
    g.set_defaults(func=cmd_gate)

    v = sub.add_parser("verify", help="no-cost preflight")
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except guards.GuardViolation as exc:
        print(f"\nGUARD VIOLATION: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
