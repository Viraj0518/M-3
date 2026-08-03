"""The sensing gate: DISTANCE-based novelty, deterministic no-key embedder."""

from __future__ import annotations

import pytest

from app.bridge import graphstore
from memory import config
from realtime import gate as gate_mod

from .conftest import requires_falkordb


def test_deterministic_embedder_is_stable_and_correct_width():
    emb = gate_mod.DeterministicEmbedder(config.EMBED_DIM)
    a = emb.embed("clean up references")
    b = emb.embed("clean up references")
    assert a == b, "same text must map to the identical vector (parity depends on it)"
    assert len(a) == config.EMBED_DIM
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6, "vector is L2-normalized"
    assert emb.embed("clean up references") != emb.embed("added a new section"), "different text separates"


def test_openai_embedder_raises_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        gate_mod.make_embedder("openai")


@requires_falkordb
def test_cold_start_admits_everything(rt_graph):
    graphstore.ensure_indexes(rt_graph)
    gate = gate_mod.SensingGate(gate_mod.DeterministicEmbedder(), graph_key=rt_graph)
    d = gate.decide("a brand new observation")
    assert d.admit is True and d.cold_start is True, "no neighbour yet -> fully novel -> admit"


@requires_falkordb
def test_exact_duplicate_is_gated_and_novel_is_admitted(rt_graph):
    graphstore.ensure_indexes(rt_graph)
    embedder = gate_mod.DeterministicEmbedder()
    summary = "categorised the page under 15th-century military figures"
    vec = embedder.embed(summary)
    graphstore.mutate(
        "MERGE (e:Event {id: 'seed'}) SET e.summary = $s, e.emb = vecf32($v)",
        {"s": summary, "v": vec},
        graph_key=rt_graph,
    )
    gate = gate_mod.SensingGate(embedder, graph_key=rt_graph)

    # the SAME summary is a near-zero DISTANCE -> below threshold -> gated OUT
    dup = gate.decide(summary)
    assert dup.cold_start is False
    assert dup.novelty < config.SALIENCE_THRESHOLD
    assert dup.admit is False, "an exact duplicate must be gated (distance ~0 < threshold)"

    # a very different summary is far -> above threshold -> admitted
    novel = gate.decide("a completely unrelated topic about quantum chromodynamics and gluons")
    assert novel.novelty >= config.SALIENCE_THRESHOLD
    assert novel.admit is True
