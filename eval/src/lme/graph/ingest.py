"""Per-question graph build: sessions -> turns -> claims -> supersede lineage.

FIVE STEPS (design doc S3.4):
  1. `haystack_dates[i]` -> `Session{date_iso, ordinal}`, wired `[:NEXT]` IN DATE
     ORDER. *Both prior arms parsed the dates and then never stamped them onto
     anything*, which structurally capped temporal-reasoning -- the largest
     bucket in the benchmark (n=133). Fixing it is the largest free win here.
  2. Every turn -> `:Turn{role, text, ordinal, ts}`. **ASSISTANT TURNS ARE
     INDEXED TOO.** The official retrieval baseline drops all 56
     single-session-assistant questions precisely because it indexes user turns
     only; that is an advantage over published RETRIEVAL baselines and not over
     full-context ones, and the report must say so rather than bank it quietly.
  3. ONE extractor call PER SESSION (25k calls across full-500, not 500k).
     Cached by `sha256(prompt|session_text|model)` so ingest is paid ONCE and all
     four ablation arms reuse it for free.
  4. **Supersede pass: DETERMINISTIC, NO LLM.** Group by
     `(subject_entity_norm, predicate)`; within a group of >=2 with differing
     text, sort by `valid_from` and write `(newer)-[:RELATES{relation:'supersedes'}]->(older)`;
     `contradicts` for same-timestamp conflicts. Auditable -- a judge can read
     the chain -- and, being code rather than a model, it cannot drift.
  5. Embed `Claim.text` and `Turn.text` (cached).

The `test_extractor` flag is opt-in ONLY, exactly like `test_embedder` and
`test_reader`. It never activates because a key is missing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import EVAL_ROOT
from ..dataset import Question, Session, strip_gold
from ..embed import Embedder
from .schema import (
    CLAIM_KINDS,
    GraphHandle,
    graph_key,
    normalize_entity,
    normalize_predicate,
)

EXTRACT_PROMPT_PATH: Path = EVAL_ROOT / "prompts" / "extract_claims_v1.md"
EXTRACT_CACHE_DIR: Path = Path(
    os.environ.get("LME_EXTRACT_CACHE", str(EVAL_ROOT / ".cache" / "extractions"))
)
TEST_EXTRACTOR_ID: str = "heuristic-test-extractor"


class ExtractorConfigError(RuntimeError):
    pass


@dataclass
class Claim:
    id: str
    text: str
    predicate: str
    kind: str
    entities: List[str]
    subject: str
    from_turn: int
    session_id: str
    valid_from: str
    polarity: str = "positive"

    def to_params(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "predicate": self.predicate,
            "kind": self.kind,
            "valid_from": self.valid_from,
            "polarity": self.polarity,
        }


# ── extractors ──────────────────────────────────────────────────────────────
def load_extract_prompt(path: Path = EXTRACT_PROMPT_PATH) -> Tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    _, rest = text.split("---SYSTEM---", 1)
    system, user = rest.split("---USER---", 1)
    return system.strip(), user.strip()


def _cache_key(model: str, prompt: str, session_text: str) -> str:
    h = hashlib.sha256()
    for part in (model, prompt, session_text):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Extractor:
    model_id: str = "abstract"

    def __init__(self) -> None:
        self.stats: Dict[str, int] = {"calls": 0, "hits": 0, "tokens_in": 0, "tokens_out": 0}

    def extract(self, session: Session) -> List[Claim]:  # pragma: no cover
        raise NotImplementedError

    @staticmethod
    def _mk_claims(session: Session, raw_claims: Sequence[Dict[str, Any]]) -> List[Claim]:
        out: List[Claim] = []
        for i, rc in enumerate(raw_claims):
            text = str(rc.get("text", "")).strip()
            if not text:
                continue
            ents = [str(e) for e in (rc.get("entities") or []) if str(e).strip()]
            kind = str(rc.get("kind", "fact")).lower()
            if kind not in CLAIM_KINDS:
                kind = "fact"
            subject = normalize_entity(ents[0]) if ents else ""
            try:
                from_turn = int(rc.get("from_turn", 0))
            except Exception:
                from_turn = 0
            from_turn = max(0, min(from_turn, len(session.turns) - 1)) if session.turns else 0
            out.append(
                Claim(
                    id=f"{session.session_id}::c{i}",
                    text=text,
                    predicate=normalize_predicate(str(rc.get("predicate", ""))),
                    kind=kind,
                    entities=[normalize_entity(e) for e in ents if normalize_entity(e)],
                    subject=subject,
                    from_turn=from_turn,
                    session_id=session.session_id,
                    valid_from=str(rc.get("valid_from") or session.date_iso[:10]),
                )
            )
        return out


class AnthropicExtractor(Extractor):
    """One call per session, cached on disk by content hash."""

    def __init__(self, model: str, *, cache_dir: Path = EXTRACT_CACHE_DIR) -> None:
        super().__init__()
        self.model_id = model
        self.cache_dir = Path(cache_dir)
        self.system, self.user_template = load_extract_prompt()
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ExtractorConfigError(
                "ANTHROPIC_API_KEY is unset. No silent fallback to the heuristic "
                "extractor -- pass `test_extractor: true` to opt in explicitly."
            )
        import anthropic

        self._client = anthropic.Anthropic(api_key=key)

    def extract(self, session: Session) -> List[Claim]:
        turns = "\n".join(
            f"[{i}] {t['role']}: {t['text']}" for i, t in enumerate(strip_gold(session))
        )
        user = (
            self.user_template.replace("{session_date}", session.date_iso[:10])
            .replace("{session_id}", session.session_id)
            .replace("{turns}", turns)
        )
        ck = _cache_key(self.model_id, self.system + user[:0], turns)
        cpath = self.cache_dir / ck[:2] / f"{ck}.json"
        if cpath.exists():
            self.stats["hits"] += 1
            return self._mk_claims(session, json.loads(cpath.read_text()).get("claims", []))

        resp = self._client.messages.create(
            model=self.model_id,
            max_tokens=2048,
            temperature=0.0,
            system=self.system,
            messages=[{"role": "user", "content": user}],
        )
        served = str(getattr(resp, "model", "") or "")
        if served != self.model_id:
            raise ExtractorConfigError(
                f"extractor served {served!r} != pinned {self.model_id!r} (T2). "
                "Claims from a different model are not comparable; refusing."
            )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        self.stats["calls"] += 1
        usage = getattr(resp, "usage", None)
        self.stats["tokens_in"] += int(getattr(usage, "input_tokens", 0) or 0)
        self.stats["tokens_out"] += int(getattr(usage, "output_tokens", 0) or 0)
        obj = _parse_claims_json(text)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps(obj))
        return self._mk_claims(session, obj.get("claims", []))


def _parse_claims_json(text: str) -> Dict[str, Any]:
    """Tolerant of a stray markdown fence; returns `{"claims": []}` on garbage.

    An unparseable extraction is an EMPTY session, not a crash: one bad session
    out of 25,000 must not kill a 50-minute ingest. The count of empty
    extractions is surfaced in `IngestReport.empty_extractions` so silent mass
    failure is still visible.
    """
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) and "claims" in obj else {"claims": []}
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) and "claims" in obj else {"claims": []}
            except Exception:
                pass
    return {"claims": []}


class HeuristicExtractor(Extractor):
    """No-LLM, deterministic. **Opt-in via `test_extractor: true` only.**

    One claim per informative turn; predicate is a crude head-noun slot; entities
    are capitalised spans plus long content words. Good enough to build a real
    graph with real edges and real vector hits so the whole A3 path executes for
    free -- and NOT good enough for a number, which is why any run using it is
    stamped non-citable in the manifest.
    """

    model_id = TEST_EXTRACTOR_ID

    _STOP = frozenset(
        """the a an and or but if of to in on at for with about from by as is are was were be i
        my me you your it its that this these those there their they them he she his her had has
        have will would could should can may might not no yes do does did what when where who how
        why very really just so too also then than
        """.split()
    )

    def extract(self, session: Session) -> List[Claim]:
        self.stats["calls"] += 1
        raw: List[Dict[str, Any]] = []
        for i, t in enumerate(strip_gold(session)):
            body = t["text"].strip()
            if len(body) < 25:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", body):
                sent = sent.strip()
                if len(sent) < 25:
                    continue
                ents = self._entities(sent)
                if not ents:
                    continue
                raw.append(
                    {
                        "text": f"{t['role']}: {sent}",
                        "predicate": self._predicate(sent, ents),
                        "kind": self._kind(sent),
                        "entities": ents[:6],
                        "from_turn": i,
                        "valid_from": session.date_iso[:10],
                    }
                )
                if len(raw) >= 40:  # bound the dry graph
                    break
            if len(raw) >= 40:
                break
        return self._mk_claims(session, raw)

    @classmethod
    def _entities(cls, sent: str) -> List[str]:
        caps = re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}(?:\s+[A-Z][a-zA-Z0-9]{2,})*", sent)
        words = [
            w
            for w in re.findall(r"[A-Za-z][A-Za-z0-9']{4,}", sent)
            if w.lower() not in cls._STOP
        ]
        seen, out = set(), []
        for e in caps + words:
            n = normalize_entity(e)
            if n and n not in seen:
                seen.add(n)
                out.append(e)
        return out

    @classmethod
    def _predicate(cls, sent: str, ents: Sequence[str]) -> str:
        head = normalize_entity(ents[0]).split(" ")[0] if ents else "topic"
        verbs = [w for w in re.findall(r"\b(is|was|have|has|got|bought|use|prefer|like|work|live|study)\b", sent.lower())]
        return normalize_predicate(f"{head}_{verbs[0] if verbs else 'attr'}")

    @staticmethod
    def _kind(sent: str) -> str:
        s = sent.lower()
        if re.search(r"\b(prefer|like|love|hate|favorite|favourite|rather|enjoy|dislike|want)\b", s):
            return "preference"
        if re.search(r"\b(yesterday|today|last week|ago|on \w+day|went|visited|happened|attended)\b", s):
            return "event"
        return "fact"


def build_extractor(*, model: str = "", test_extractor: bool = False) -> Extractor:
    """THE factory. `test_extractor` is the ONLY route to the heuristic path."""
    if test_extractor:
        return HeuristicExtractor()
    if not model:
        raise ExtractorConfigError("extractor model is unset and test_extractor is false")
    return AnthropicExtractor(model)


# ── supersede pass (deterministic, no LLM) ──────────────────────────────────
@dataclass
class Lineage:
    newer: str
    older: str
    relation: str


def supersede_pass(claims: Sequence[Claim]) -> List[Lineage]:
    """Group by `(subject, predicate)`; order by `valid_from`; chain them.

    NO LLM. That is the point: the knowledge-update mechanism is CODE, so it is
    auditable (a judge can read the chain), reproducible bit-for-bit, and cannot
    drift between runs. It is also removable in one flag, which is what makes
    `a3_ablate_supersede` a real removal test rather than a prompt tweak.

    Same-`valid_from` conflicts get `contradicts` rather than `supersedes`: with
    no time ordering there is no "newer", and inventing one would fabricate the
    very signal the mechanism claims to measure.
    """
    groups: Dict[Tuple[str, str], List[Claim]] = {}
    for c in claims:
        if not c.subject or not c.predicate:
            continue
        groups.setdefault((c.subject, c.predicate), []).append(c)

    out: List[Lineage] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        if len({m.text for m in members}) < 2:
            continue  # same statement repeated is not an update
        ordered = sorted(members, key=lambda m: (m.valid_from, m.session_id, m.id))
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.text == nxt.text:
                continue
            rel = "contradicts" if prev.valid_from == nxt.valid_from else "supersedes"
            out.append(Lineage(newer=nxt.id, older=prev.id, relation=rel))
    return out


# ── the build ───────────────────────────────────────────────────────────────
@dataclass
class IngestReport:
    graph_key: str
    n_sessions: int = 0
    n_turns: int = 0
    n_claims: int = 0
    n_entities: int = 0
    n_supersedes: int = 0
    n_contradicts: int = 0
    empty_extractions: int = 0
    dated_sessions: int = 0

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def ingest_question(
    question: Question,
    *,
    split_id: str,
    embedder: Embedder,
    extractor: Extractor,
    ablate_supersede: bool = False,
    ablate_temporal: bool = False,
    fresh: bool = True,
) -> Tuple[GraphHandle, IngestReport]:
    """Build (or rebuild) this question's isolated graph.

    `ablate_temporal` drops `Session.date_iso` and the `[:NEXT]` spine at INGEST
    time, not at query time. That matters: an ablation that only disables a query
    stage while the data stays dated is not a removal test, because the reader
    still sees dates in the served context via S5.
    """
    key = graph_key(split_id, question.question_id)
    g = GraphHandle(key)
    if fresh:
        g.drop()
        g = GraphHandle(key)
    g.ensure_schema(dim=embedder.dim)  # indexes BEFORE nodes (schema.py trap 2)

    rep = IngestReport(graph_key=key, n_sessions=len(question.sessions))

    # 1. sessions + temporal spine
    for s in question.sessions:
        date_iso = "" if ablate_temporal else s.date_iso
        g.query(
            "CREATE (:Session {id:$id, date_iso:$d, date_raw:$dr, ordinal:$o})",
            {
                "id": s.session_id,
                "d": date_iso,
                "dr": "" if ablate_temporal else s.date_raw,
                "o": -1 if ablate_temporal else s.ordinal,
            },
        )
        if date_iso:
            rep.dated_sessions += 1
    if not ablate_temporal:
        ordered = [s for s in question.sessions if s.date_iso]
        for a, b in zip(ordered, ordered[1:]):
            g.query(
                "MATCH (x:Session {id:$a}), (y:Session {id:$b}) CREATE (x)-[:NEXT]->(y)",
                {"a": a.session_id, "b": b.session_id},
            )

    # 2. turns -- BOTH roles indexed
    turn_texts: List[str] = []
    turn_rows: List[Dict[str, Any]] = []
    for s in question.sessions:
        for t in strip_gold(s):
            tid = f"{s.session_id}::t{len(turn_rows)}"
            turn_rows.append(
                {
                    "id": tid,
                    "role": t["role"],
                    "text": t["text"],
                    "ordinal": len(turn_rows),
                    "ts": "" if ablate_temporal else s.date_iso,
                    "sid": s.session_id,
                }
            )
            turn_texts.append(f"{t['role']}: {t['text']}")
    turn_vecs = embedder.embed(turn_texts) if turn_texts else []
    for row, vec in zip(turn_rows, turn_vecs):
        g.query(
            "MATCH (s:Session {id:$sid}) "
            "CREATE (t:Turn {id:$id, role:$role, text:$text, ordinal:$ordinal, ts:$ts, "
            "emb: vecf32($emb)})-[:IN_SESSION]->(s)",
            {**row, "emb": [float(x) for x in vec]},
        )
    rep.n_turns = len(turn_rows)

    # 3. claims -- ONE extractor call per session
    all_claims: List[Claim] = []
    turn_id_by = {(r["sid"], i): r["id"] for i, r in enumerate(turn_rows)}
    per_session_turn_ids: Dict[str, List[str]] = {}
    for r in turn_rows:
        per_session_turn_ids.setdefault(r["sid"], []).append(r["id"])

    for s in question.sessions:
        claims = extractor.extract(s)
        if not claims:
            rep.empty_extractions += 1
        for c in claims:
            c.id = f"{s.session_id}::c{len(all_claims)}"
            all_claims.append(c)

    claim_vecs = embedder.embed([c.text for c in all_claims]) if all_claims else []
    for c, vec in zip(all_claims, claim_vecs):
        tids = per_session_turn_ids.get(c.session_id, [])
        tid = tids[min(c.from_turn, len(tids) - 1)] if tids else None
        g.query(
            "CREATE (:Claim {id:$id, text:$text, predicate:$predicate, kind:$kind, "
            "valid_from:$valid_from, polarity:$polarity, emb: vecf32($emb)})",
            {**c.to_params(), "emb": [float(x) for x in vec]},
        )
        if tid:
            g.query(
                "MATCH (c:Claim {id:$cid}), (t:Turn {id:$tid}) CREATE (c)-[:FROM_TURN]->(t)",
                {"cid": c.id, "tid": tid},
            )
        for e in c.entities:
            g.query("MERGE (:Entity {norm:$n})", {"n": e})
            g.query(
                "MATCH (c:Claim {id:$cid}), (e:Entity {norm:$n}) MERGE (c)-[:MENTIONS]->(e)",
                {"cid": c.id, "n": e},
            )
    rep.n_claims = len(all_claims)
    rep.n_entities = g.node_count("Entity")

    # 4. supersede lineage
    if not ablate_supersede:
        for lin in supersede_pass(all_claims):
            g.query(
                "MATCH (a:Claim {id:$new}), (b:Claim {id:$old}) "
                "CREATE (a)-[:RELATES {relation:$rel}]->(b)",
                {"new": lin.newer, "old": lin.older, "rel": lin.relation},
            )
            if lin.relation == "supersedes":
                rep.n_supersedes += 1
            else:
                rep.n_contradicts += 1

    return g, rep


__all__ = [
    "AnthropicExtractor",
    "Claim",
    "EXTRACT_PROMPT_PATH",
    "Extractor",
    "ExtractorConfigError",
    "HeuristicExtractor",
    "IngestReport",
    "Lineage",
    "TEST_EXTRACTOR_ID",
    "build_extractor",
    "ingest_question",
    "load_extract_prompt",
    "supersede_pass",
]
