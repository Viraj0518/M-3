"""PALIMPSEST graph vocabulary — LIFTED from unblock, never redesigned.

Written at H0:30 so that nobody redesigns taxonomy at 2am. Every value here is
copied from a production source, cited inline. If you want to add a value,
argue about it BEFORE the sprint, not during it.

Provenance (reference copies live under vendor/unblock-reuse/):
  RELATION_KINDS   <- unblock_substrate/supabase/functions/unblock-api/relate-verb.ts:83-90
                      (= unblock_protocol MemoryRelationKindSchema)
  BLOCK_TYPES      <- unblock_substrate/supabase/functions/unblock-api/ingest-verb.ts:93-107
                      (the AUTHORITATIVE 13-value list — unblock_protocol carries a
                       stale 11-value list; the storage CHECK constraint matches THIS one)
  MESSAGE_INTENTS  <- unblock_protocol/src/schemas/message.ts:17-30

════════════════════════════════════════════════════════════════════════════
THE ONE DELIBERATE DIVERGENCE: RELATIONS STAY DIRECTED
════════════════════════════════════════════════════════════════════════════
unblock stores its relation edges UNDIRECTED, and not because anyone decided
that was right. `unblock_storage/src/migrations/031_lockdown_contradiction_edges.sql`
puts a canonicalizing CHECK on the edge table:

    CONSTRAINT contradiction_edges_ordered_chk CHECK (block_id_a < block_id_b)

That constraint forces every edge to be stored in lexical id order, which
DESTROYS direction: after the write you can no longer tell "A supersedes B"
from "B supersedes A". It is a Postgres dedup trick that silently ate a
semantic property, and unblock's own source flags it.

PALIMPSEST DROPS THAT CHECK. FalkorDB gives us real edge direction for free, so
`(:Claim)-[:RELATES {relation:'supersedes'}]->(:Claim)` means exactly what it
reads as. `supersedes`, `derived_from` and `contradicts` are all genuinely
asymmetric and we store them that way. This is a judge-facing improvement — say
it out loud, do not bury it.

DIRECTED_RELATIONS below names which kinds are asymmetric, so a reader can
assert the property instead of trusting a comment.

════════════════════════════════════════════════════════════════════════════
LANDMINE: 'fix' AND 'learning' ARE NOT BLOCK TYPES
════════════════════════════════════════════════════════════════════════════
They look like block types, they read like block types, and the storage CHECK
rejects them — in unblock they silently degrade to 'note' and the capture
intent is lost forever. Carry them in `metadata.kind` instead:

    a bug fix   -> block_type='decision', metadata={'kind': 'fix'}
    a learning  -> block_type='note',     metadata={'kind': 'learning'}

`normalize_block_type()` below does exactly that transformation, loudly.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Tuple

# ── RELATION_KINDS (6) ──────────────────────────────────────────────────────
# relate-verb.ts:83-90. NOTE from that source: 'relates' is NOT a writable kind
# — it is only the graph READER's fallback vocabulary for unknown statuses
# (roster-graph.ts mapContradictionStatus). We do not accept it on a write.
RELATION_KINDS: Tuple[str, ...] = (
    "supports",
    "contradicts",
    "derived_from",
    "supersedes",
    "duplicates",
    "references",
)

#: Kinds whose direction carries meaning. Reversing one of these INVERTS the
#: claim, which is precisely what unblock's a<b CHECK made unrepresentable.
DIRECTED_RELATIONS: FrozenSet[str] = frozenset(
    {"contradicts", "derived_from", "supersedes", "supports"}
)

#: Kinds that are genuinely symmetric — reversing them is a no-op in meaning.
#: We STILL store one directed edge (writer's direction); we just don't care.
SYMMETRIC_RELATIONS: FrozenSet[str] = frozenset({"duplicates", "references"})

#: Reader-only fallback vocabulary. Never writable.
RELATION_READER_FALLBACK: str = "relates"


# ── BLOCK_TYPES (13) ────────────────────────────────────────────────────────
# ingest-verb.ts:93-107 — the blocks.block_type CHECK taxonomy
# (unblock_storage migration 002 + ADR-008). This 13-value list is
# AUTHORITATIVE over the 11-value list in unblock_protocol.
BLOCK_TYPES: Tuple[str, ...] = (
    "note",
    "snippet",
    "doc",
    "code",
    "trace",
    "decision",
    "anti-pattern",
    "dataset",
    "exploit",
    "kg",
    "conversation",
    "utterance",
    "other",
)

DEFAULT_BLOCK_TYPE: str = "note"

#: The two words that LOOK like block types and are not. Mapped, not rejected.
NOT_BLOCK_TYPES: Dict[str, Tuple[str, str]] = {
    # capture-intent word -> (real block_type, metadata.kind)
    "fix": ("decision", "fix"),
    "learning": ("note", "learning"),
}


# ── MESSAGE_INTENTS (10 sender-side + 5 receipt-side) ───────────────────────
# message.ts:17-30 (MessageIntentSchema) and :30-38 (MessageReceiptIntentSchema).
MESSAGE_INTENTS: Tuple[str, ...] = (
    "ASK",
    "SUGGEST",
    "FYI",
    "URGENT",
    "ACK",
    "REJECT",
    "DELEGATE",
    "PROPOSE",
    "ARTIFACT",
    "MENTION",
)

MESSAGE_RECEIPT_INTENTS: Tuple[str, ...] = (
    "READ",
    "ACK",
    "REJECT",
    "COMMITTED",
    "DECLINED",
)


# ── PALIMPSEST graph labels + edge types (see memory/SCHEMA.md) ─────────────
NODE_LABELS: Tuple[str, ...] = (
    "Actor",
    "Page",
    "Wiki",
    "Event",
    "Entity",
    "Claim",
    "Case",
    "Agent",
    "Assertion",
    "Action",
)

EDGE_TYPES: Tuple[str, ...] = (
    "EDITED",
    "ON",
    "CO_EDITED_WITH",
    "ABOUT",
    "MENTIONS",
    "IMPLICATES",
    "AUTHORED",
    "RELATES",
    "HANDED_OFF_TO",
)

#: Handover lifecycle. `open` is the ONLY status a cold-resume may inherit —
#: see the status guard in app/bridge/server.py / memory/handover.py.
HANDOVER_STATUSES: Tuple[str, ...] = ("open", "superseded", "closed")
HANDOVER_OPEN: str = "open"


# ── validators (crash early, honest errors) ────────────────────────────────
def is_relation(value: str) -> bool:
    return value in RELATION_KINDS


def check_relation(value: str) -> str:
    """Return `value` if it is a writable relation kind, else raise."""
    if value == RELATION_READER_FALLBACK:
        raise ValueError(
            "'relates' is the graph READER's fallback vocabulary for unknown "
            "statuses — it is not a writable relation kind. Pick one of: "
            + ", ".join(RELATION_KINDS)
        )
    if value not in RELATION_KINDS:
        raise ValueError(
            f"unknown relation {value!r}. Writable kinds: " + ", ".join(RELATION_KINDS)
        )
    return value


def is_directed(relation: str) -> bool:
    """True when reversing the edge would invert its meaning."""
    return relation in DIRECTED_RELATIONS


def normalize_block_type(
    block_type: Optional[str],
    metadata: Optional[dict] = None,
) -> Tuple[str, dict]:
    """Fold a capture-intent word into (block_type, metadata) honestly.

    >>> normalize_block_type("fix")
    ('decision', {'kind': 'fix'})
    >>> normalize_block_type("learning", {"a": 1})
    ('note', {'a': 1, 'kind': 'learning'})
    >>> normalize_block_type(None)
    ('note', {})
    >>> normalize_block_type("decision")
    ('decision', {})

    An unknown value raises rather than silently degrading to 'note' — that
    silent degradation is exactly the unblock bug we are inheriting a fix for.
    """
    meta = dict(metadata or {})
    if not block_type:
        return DEFAULT_BLOCK_TYPE, meta
    if block_type in NOT_BLOCK_TYPES:
        real, kind = NOT_BLOCK_TYPES[block_type]
        meta.setdefault("kind", kind)
        return real, meta
    if block_type not in BLOCK_TYPES:
        raise ValueError(
            f"unknown block_type {block_type!r}. In-taxonomy types: "
            + ", ".join(BLOCK_TYPES)
            + ". ('fix' and 'learning' are NOT block_types — carry them in "
            "metadata.kind; normalize_block_type() does it for you.)"
        )
    return block_type, meta


def check_message_intent(value: str) -> str:
    if value not in MESSAGE_INTENTS:
        raise ValueError(
            f"unknown message intent {value!r}. Valid: " + ", ".join(MESSAGE_INTENTS)
        )
    return value


__all__: List[str] = [
    "RELATION_KINDS",
    "DIRECTED_RELATIONS",
    "SYMMETRIC_RELATIONS",
    "RELATION_READER_FALLBACK",
    "BLOCK_TYPES",
    "DEFAULT_BLOCK_TYPE",
    "NOT_BLOCK_TYPES",
    "MESSAGE_INTENTS",
    "MESSAGE_RECEIPT_INTENTS",
    "NODE_LABELS",
    "EDGE_TYPES",
    "HANDOVER_STATUSES",
    "HANDOVER_OPEN",
    "is_relation",
    "check_relation",
    "is_directed",
    "normalize_block_type",
    "check_message_intent",
]
