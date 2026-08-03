"""The run loop: dataset -> split -> arm -> reader -> rows -> guards -> aggregate.

ORDER OF OPERATIONS IS THE GUARD. Nothing is aggregated, and nothing is printed,
until T3 (complete), T1 (fresh), and T2 (served model) have all passed. That
ordering is what the 0.9010 phantom defeated: it aggregated first and asserted
never, so a stale file read cleanly.

Artifacts per run dir:
    manifest.json     the reproducibility contract
    rows.jsonl        one per question (prediction + provenance + served_model)
    retrieval.jsonl   JUDGE-FREE recall -- the GO/NO-GO gate
    aggregate.json    qa.* and retrieval.* metrics, namespaced (T6)
    judged.<lane>.json  written by the judge lanes
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from tqdm import tqdm

from . import dataset as ds
from . import guards, metrics
from .arms import build_arm
from .config import config_hash_inputs, load_config
from .embed import build_embedder
from .manifest import Manifest, Pins, Sampling
from .reader import build_reader, load_reader_prompt


def _pins_from_cfg(cfg: Mapping[str, Any]) -> Pins:
    p = dict(cfg.get("pins") or {})
    return Pins(
        embedder=str(p.get("embedder", "")),
        extractor=str(p.get("extractor", "")),
        reader=str(p.get("reader", "")),
        judge_official=str(p.get("judge_official", "gpt-4o-2024-08-06")),
        judge_strict_panel=list(p.get("judge_strict_panel") or []),
    )


def _sampling_from_cfg(cfg: Mapping[str, Any]) -> Sampling:
    s = dict(cfg.get("sampling") or {})
    return Sampling(
        temperature=float(s.get("temperature", 0.0)),
        top_p=float(s.get("top_p", 1.0)),
        reader_max_tokens=int(s.get("reader_max_tokens", 512)),
    )


#: The dry overlay. Applied ON TOP of the arm's own config, never INSTEAD of it
#: -- otherwise `--dry --ablations` would silently run four identical
#: non-ablated arms and the removal-test wiring would never be exercised.
DRY_OVERLAY: Dict[str, Any] = {
    "test_embedder": True,
    "test_reader": True,
    "test_extractor": True,
}


def run_arm(
    *,
    arm_name: str,
    config_name: str,
    data_path: Path,
    split_name: str,
    seed: int = 20260803,
    limit: int = 0,
    overrides: Optional[Mapping[str, Any]] = None,
    progress: bool = True,
    runs_dir: Optional[Path] = None,
    dry: bool = False,
) -> Dict[str, Any]:
    """Execute one arm over one split. Returns a summary dict."""
    cfg = load_config(config_name)
    if dry:
        cfg.update(DRY_OVERLAY)
        # keep the arm's real mechanism settings (incl. `ablate:`) intact
        cfg["_config_files"] = list(cfg.get("_config_files", [])) + ["<dry-overlay>"]
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    cfg["arm"] = arm_name

    # ── dataset + split: T9 and T5 both fire HERE, before any work ──────────
    data = ds.load_dataset(data_path)
    split, questions = ds.resolve(data, split_name)
    if limit:
        questions = questions[:limit]

    test_embedder = bool(cfg.get("test_embedder", False))
    test_reader = bool(cfg.get("test_reader", False))
    test_extractor = bool(cfg.get("test_extractor", False))
    is_dry = test_embedder or test_reader or test_extractor

    pins = _pins_from_cfg(cfg)
    embedder = build_embedder(test_embedder=test_embedder)
    pins.embedder = embedder.pin

    reader = build_reader(
        model=pins.reader if not test_reader else "",
        test_reader=test_reader,
        temperature=float((cfg.get("sampling") or {}).get("temperature", 0.0)),
        max_tokens=int((cfg.get("sampling") or {}).get("reader_max_tokens", 512)),
    )
    pins.reader = reader.model_id

    deps: Dict[str, Any] = {"embedder": embedder, "split_id": data.split_id}
    if arm_name.startswith("a3_"):
        from .graph.ingest import build_extractor

        extractor = build_extractor(
            model=pins.extractor if not test_extractor else "",
            test_extractor=test_extractor,
        )
        pins.extractor = extractor.model_id
        deps["extractor"] = extractor

    arm = build_arm(arm_name, cfg, **deps)

    man = Manifest(
        arm=arm_name,
        dataset_file=data.name,
        dataset_sha256=data.sha256,
        hf_repo=ds.HF_REPO,
        hf_revision=ds.HF_REVISION,
        split_name=split.name,
        split_n=split.n,
        split_ids_sha256=split.ids_sha256,
        seed=seed,
        expected_n=len(questions),
        pins=pins,
        sampling=_sampling_from_cfg(cfg),
    )
    man.compute_config_hash(config_hash_inputs(cfg))
    man.check_config_hash()
    # T7 is checked only for a REAL run: the dry path's `extractive-test-reader`
    # has no model family, and asserting family exclusion on a stub would be a
    # green that means nothing.
    if not is_dry:
        man.check_pins()
    else:
        man.notes.append(
            "DRY RUN -- test_embedder/test_reader/test_extractor were opted into "
            "EXPLICITLY. This run proves PLUMBING ONLY. Its qa.* numbers are "
            "MEANINGLESS and NON-CITABLE; retrieval.* is real graph behaviour "
            "over a non-semantic embedder."
        )
        man.notes.append(f"dry flags: embedder={test_embedder} reader={test_reader} extractor={test_extractor}")

    system, user_template = load_reader_prompt()
    run_dir = Path(runs_dir) if runs_dir else man.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    retr: List[Dict[str, Any]] = []
    ret_rows: List[metrics.RetrievalRow] = []

    it = tqdm(questions, desc=f"{arm_name}/{split_name}", disable=not progress)
    for q in it:
        t0 = time.perf_counter()
        arm.prepare(q)
        out = arm.retrieve(q)
        res = arm.answer(q, out, reader=reader, system=system, user_template=user_template)
        arm.teardown(q)
        wall = int((time.perf_counter() - t0) * 1000)

        rows.append(
            {
                "run_id": man.run_id,
                "question_id": q.question_id,
                "question_type": q.question_type,
                "is_abstention": q.is_abstention,
                "question": q.question,
                "gold": q.answer,
                "prediction": res.text,
                "prediction_raw": res.raw,
                "predicted_abstain": res.abstained,
                "retrieved_session_ids": out.ranked_session_ids,
                "retrieved_block_ids": out.retrieved_block_ids,
                "returned_set_size": out.returned_set_size,
                "n_context_chars": len(out.context),
                "latency_ms": res.latency_ms,
                "wall_ms": wall,
                "served_model": res.served_model,
                "diagnostics": out.diagnostics,
            }
        )
        rr = metrics.RetrievalRow(
            question_id=q.question_id,
            question_type=q.question_type,
            gold_session_ids=list(q.gold_session_ids),
            ranked_session_ids=list(out.ranked_session_ids),
            returned_set_size=out.returned_set_size,
        )
        ret_rows.append(rr)
        retr.append(
            {
                "run_id": man.run_id,
                "question_id": q.question_id,
                "question_type": q.question_type,
                "gold_session_ids": rr.gold_session_ids,
                "ranked_session_ids": rr.ranked_session_ids,
                "returned_set_size": rr.returned_set_size,
                **{f"recall@{k}": rr.recall_at(k) for k in (1, 5, 10, arm.budget)},
            }
        )

    rows_path = run_dir / "rows.jsonl"
    rows_path.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n")
    retr_path = run_dir / "retrieval.jsonl"
    retr_path.write_text("\n".join(json.dumps(r, default=str) for r in retr) + "\n")

    # ── retrieval metrics FIRST: judge-free, deterministic, the GO/NO-GO gate ─
    ret_metrics = metrics.retrieval_metrics(
        ret_rows, split_id=data.split_id, final_top_k=arm.budget
    )

    # T3/T1/T2 before ANY aggregate is quoted.
    man.finalize(
        rows,
        artifact_paths=[rows_path, retr_path],
        scores=None,
        enforce_served_model=not is_dry,
    )
    man.cost = {
        "usd_est": 0.0,
        "tokens_in": float(getattr(reader, "stats", {}).get("tokens_in", 0)),
        "tokens_out": float(getattr(reader, "stats", {}).get("tokens_out", 0)),
    }
    man.notes.append(f"embed cache hits={embedder.stats['hits']} misses={embedder.stats['misses']}")
    man.write(run_dir)

    aggregate = {
        "arm": arm_name,
        "split_id": data.split_id,
        "budget": arm.budget,
        "judged": False,
        "note": "qa.* requires a judge lane -- run `uv run eval judge`.",
        **ret_metrics,
    }
    (run_dir / "aggregate.json").write_text(json.dumps(aggregate, indent=2, default=str) + "\n")

    return {
        "run_id": man.run_id,
        "run_dir": str(run_dir),
        "arm": arm_name,
        "n": len(rows),
        "split_id": data.split_id,
        "valid": man.valid,
        "dry": is_dry,
        "retrieval": {k: v for k, v in ret_metrics.items() if isinstance(v, float)},
    }


__all__ = ["run_arm"]
