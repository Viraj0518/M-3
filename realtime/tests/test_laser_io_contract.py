"""Contract tests for the small Laser SDK adapter seam.

These tests use fakes, so they prove routing, JSON encoding, and failure
honesty without requiring Docker, Laser Stack, or a Cloud credential.
"""

from __future__ import annotations

import asyncio

import pytest

from realtime.laser_io import LaserLog


class FakeProducer:
    def __init__(self) -> None:
        self.init_calls = 0
        self.sent = []

    async def init(self) -> None:
        self.init_calls += 1

    async def send(self, payload, *, key=None) -> None:
        self.sent.append((payload, key))


class FakeTopic:
    def __init__(self, *, fail_ensure: bool = False) -> None:
        self.fail_ensure = fail_ensure
        self.ensure_calls = []
        self.producer_instance = FakeProducer()

    async def ensure(self, *, partitions: int) -> None:
        self.ensure_calls.append(partitions)
        if self.fail_ensure:
            raise RuntimeError("ensure failed")

    def producer(self, **kwargs):
        self.producer_kwargs = kwargs
        return self.producer_instance


class FakeStream:
    def __init__(self, topic: FakeTopic) -> None:
        self._topic = topic
        self.names = []

    def topic(self, name: str) -> FakeTopic:
        self.names.append(name)
        return self._topic


class FakeLaser:
    def __init__(self, topic: FakeTopic) -> None:
        self.stream_handle = FakeStream(topic)
        self.stream_names = []

    def stream(self, name: str) -> FakeStream:
        self.stream_names.append(name)
        return self.stream_handle


def test_uses_explicit_stream_and_bytes_routing_key():
    topic = FakeTopic()
    laser = FakeLaser(topic)
    log = LaserLog(laser, "live", {})

    asyncio.run(log.publish("signal.raw", {"wiki": "enwiki"}, key="enwiki"))

    assert laser.stream_names == ["live"]
    assert laser.stream_handle.names == ["signal.raw"]
    assert topic.ensure_calls == [4]
    assert topic.producer_instance.init_calls == 1
    payload, key = topic.producer_instance.sent[0]
    assert payload == b'{"wiki": "enwiki"}'
    assert key == b"enwiki"


def test_ensure_failure_is_not_hidden():
    topic = FakeTopic(fail_ensure=True)
    log = LaserLog(FakeLaser(topic), "live", {})

    with pytest.raises(RuntimeError, match="ensure failed"):
        asyncio.run(log.publish("signal.raw", {"x": 1}))
