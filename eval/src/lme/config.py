"""Config loading with `extends:` inheritance, and the config_hash inputs.

Every value that can move a number lives in a committed YAML file and is folded
into `config_hash` (T10). The hash inputs are LABEL -> CONTENT HASH, so the
manifest records not just "which config" but "which BYTES of which config, which
prompt, and which retrieval code".

`schema.py` and `retrieve.py` are hashed as SOURCE FILES because a change to the
Cypher is a change to the mechanism. That is the difference between a config
hash and a config name.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from . import EVAL_ROOT
from .manifest import CODE_VERSION, sha256_path, sha256_text

CONFIG_DIR: Path = EVAL_ROOT / "configs"
PROMPTS_DIR: Path = EVAL_ROOT / "prompts"


def _deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = copy.deepcopy(dict(base))
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(name_or_path: str, *, _seen: Optional[set] = None) -> Dict[str, Any]:
    """Load a config, resolving a single `extends:` chain. Cycles raise."""
    seen = _seen or set()
    p = Path(name_or_path)
    if not p.is_absolute():
        p = CONFIG_DIR / (name_or_path if name_or_path.endswith(".yaml") else f"{name_or_path}.yaml")
    p = p.resolve()
    if p in seen:
        raise ValueError(f"config `extends:` cycle at {p}")
    seen.add(p)
    if not p.exists():
        raise FileNotFoundError(f"no such config: {p}")

    body = yaml.safe_load(p.read_text()) or {}
    parent_name = body.pop("extends", None)
    cfg = load_config(str(parent_name), _seen=seen) if parent_name else {}
    merged = _deep_merge(cfg, body)
    merged.setdefault("_config_files", [])
    merged["_config_files"] = list(merged["_config_files"]) + [str(p)]
    return merged


def config_hash_inputs(cfg: Mapping[str, Any]) -> Dict[str, str]:
    """LABEL -> content hash. `Manifest.check_config_hash` asserts the required
    labels are present, so a config that forgets the reader prompt cannot run."""
    import json

    files = [Path(f) for f in cfg.get("_config_files", [])]
    scrubbed = {k: v for k, v in cfg.items() if not k.startswith("_")}
    inputs: Dict[str, str] = {
        "config_yaml": sha256_text(json.dumps(scrubbed, sort_keys=True, default=str)),
        "config_files": sha256_text("|".join(sorted(sha256_path(f) for f in files))),
        "reader_prompt": sha256_path(PROMPTS_DIR / "reader_v1.md"),
        "extract_claims_prompt": sha256_path(PROMPTS_DIR / "extract_claims_v1.md"),
        "strict_rubric": sha256_path(PROMPTS_DIR / "strict_rubric_v1_1.md"),
        "code_version": sha256_text(CODE_VERSION),
    }
    # The mechanism itself: a Cypher change IS a config change.
    src = EVAL_ROOT / "src" / "lme" / "graph"
    for mod in ("schema.py", "retrieve.py", "ingest.py"):
        inputs[f"graph_{mod}"] = sha256_path(src / mod)
    return inputs


def apply_overrides(cfg: Dict[str, Any], overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """CLI overrides. Recorded in the config and therefore inside config_hash --
    a `--final-top-k 20` on the command line is a new experiment, not a flag."""
    return _deep_merge(cfg, {k: v for k, v in overrides.items() if v is not None})


__all__ = ["CONFIG_DIR", "apply_overrides", "config_hash_inputs", "load_config"]
