"""The graph writer: recentchange -> graph mapping + idempotent MERGE."""

from __future__ import annotations

import asyncio

from app.bridge import graphstore
from realtime import graph_writer as gw

from .conftest import requires_falkordb

SAMPLE = {
    "type": "edit",
    "user": "MS Sakib",
    "bot": True,
    "title": "Q357493",
    "wiki": "wikidatawiki",
    "server_name": "www.wikidata.org",
    "title_url": "https://www.wikidata.org/wiki/Q357493",
    "timestamp": 1785757268,
    "comment": "/* wbsetdescription-add:1|as */ English sprinter",
    "length": {"old": 43087, "new": 43205},
    "meta": {"id": "d0c149ad-d5f1-4633-901a-073e569dc0d2", "offset": 6384112250},
}


def test_map_event_extracts_the_model():
    m = gw.map_event(SAMPLE, offset=42)
    assert m is not None
    assert m.actor == "MS Sakib" and m.is_bot is True
    assert m.title == "Q357493" and m.wiki == "wikidatawiki"
    assert m.bytes_delta == 43205 - 43087
    assert m.event_id == "d0c149ad-d5f1-4633-901a-073e569dc0d2"
    assert m.offset == 42


def test_map_event_rejects_rows_without_actor_or_title():
    assert gw.map_event({"user": "x"}) is None       # no title
    assert gw.map_event({"title": "y"}) is None       # no user
    assert gw.map_event("not a dict") is None


def _counts(graph_key):
    rows, _ms, _h = graphstore.query(
        "MATCH (n) RETURN labels(n)[0], count(n)", graph_key=graph_key, read_only=True
    )
    return {r[0]: r[1] for r in rows}


@requires_falkordb
def test_merge_is_idempotent(rt_graph):
    writer = gw.GraphWriter(graph_key=rt_graph, use_gate=False)

    async def once():
        w = gw.GraphWriter(graph_key=rt_graph, use_gate=False)
        await w.write(SAMPLE)
        return w

    asyncio.run(writer.write(SAMPLE))
    after_first = _counts(rt_graph)

    # writing the SAME event again must not create any new node — at-least-once
    # redelivery is a no-op, which is what makes replay safe.
    asyncio.run(once())
    after_second = _counts(rt_graph)

    assert after_first == after_second, "a redelivered event created duplicate nodes"
    assert after_first.get("Actor") == 1
    assert after_first.get("Page") == 1
    assert after_first.get("Event") == 1
    assert after_first.get("Wiki") == 1


@requires_falkordb
def test_edited_edge_and_co_edited_derivation(rt_graph):
    # two actors editing the SAME page within the window -> a CO_EDITED_WITH edge
    e1 = dict(SAMPLE, user="Alice", meta={"id": "ev-a"}, timestamp=1000)
    e2 = dict(SAMPLE, user="Bob", meta={"id": "ev-b"}, timestamp=1100)  # 100s apart

    async def go():
        w = gw.GraphWriter(graph_key=rt_graph, use_gate=False)
        await w.write(e1)
        await w.write(e2)
        return w.finalize(window_s=720)

    derived = asyncio.run(go())
    assert derived["co_edited_edges"] >= 1

    rows, _ms, _h = graphstore.query(
        "MATCH (:Actor)-[c:CO_EDITED_WITH]->(:Actor) RETURN count(c)",
        graph_key=rt_graph, read_only=True,
    )
    assert rows[0][0] >= 1

    # edit_count is a pure derived count
    rows2, _ms2, _h2 = graphstore.query(
        "MATCH (a:Actor {name:'Alice'}) RETURN a.edit_count",
        graph_key=rt_graph, read_only=True,
    )
    assert rows2[0][0] == 1
