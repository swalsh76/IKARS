"""Recordings, the replay and the point-in-time rule (PLAN D20 A)."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ikars_engine.clocks import ClockWentBackwards, VirtualClock, WallClock
from ikars_engine.model import Item, Link, Ref, decode_item, encode
from ikars_engine.recorder import write_items
from ikars_engine.replay import RecordingReplay
from ikars_engine.sources import LiveSource

T0 = 1_767_225_600_000_000_000  # 2026-01-01 UTC


def item(at: int, n: int = 0, kind: str = "tick") -> Item:
    return Item(ref=Ref(kind, f"{kind}-{n}"), source="test", received_ns=at, payload={"n": n})


def test_items_round_trip_through_json() -> None:
    original = Item(
        ref=Ref("market", "M"), source="s", received_ns=5, published_ns=3, payload={"a": [1, "x"]}
    )
    assert decode_item(encode(original)) == original
    assert str(Ref("market", "M")) == "market:M"
    link = Link(
        source=Ref("source_item", "1"), target=Ref("market", "M"), kind="mentions", made_ns=9
    )
    assert b"mentions" in encode(link)


async def replayed(root: Path, start: int, end: int) -> list[Item]:
    clock = VirtualClock(start)
    return [i async for i in RecordingReplay(root, start, end, clock).items()]


async def test_recordings_replay_in_received_order_keeping_ties_stably(tmp_path: Path) -> None:
    times = [30, 10, 20, 20, 5]
    write_items(tmp_path, [item(T0 + at, n + 1) for n, at in enumerate(times)])
    back = await replayed(tmp_path, T0, T0 + 100)
    assert [i.received_ns - T0 for i in back] == [5, 10, 20, 20, 30]
    assert [i.ref.id for i in back] == ["tick-5", "tick-2", "tick-3", "tick-4", "tick-1"]
    assert await replayed(tmp_path / "none", T0, T0 + 100) == []


async def test_the_replay_never_releases_an_item_before_its_received_time(tmp_path: Path) -> None:
    write_items(tmp_path, [item(T0 + at, n) for n, at in enumerate([3, 1, 2, 2, 9])])
    clock = VirtualClock(T0)
    seen = []
    async for released in RecordingReplay(tmp_path, T0, T0 + 10, clock).items():
        assert clock.now_ns() == released.received_ns  # time reached the item first
        seen.append(released.received_ns - T0)
    assert seen == sorted(seen) == [1, 2, 2, 3, 9]


@settings(max_examples=25, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=10**15), min_size=1, max_size=40))
def test_replay_order_is_received_order_for_any_recording(times: list[int]) -> None:
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        write_items(root, [item(T0 + at, n) for n, at in enumerate(times)])

        async def collect() -> list[int]:
            return [i.received_ns - T0 for i in await replayed(root, T0, T0 + 10**15 + 1)]

        assert asyncio.run(collect()) == sorted(times)


def test_the_virtual_clock_never_goes_back() -> None:
    clock = VirtualClock(10)
    clock.advance_to(10)
    with pytest.raises(ClockWentBackwards):
        clock.advance_to(9)


async def test_the_wall_clock_moves_forward() -> None:
    clock = WallClock()
    first = clock.monotonic_ns()
    await clock.sleep(0)
    assert clock.monotonic_ns() >= first and clock.now_ns() > 0


async def test_a_live_source_gives_items_in_arrival_order_until_closed() -> None:
    source = LiveSource()
    await source.put(item(2, 1))
    await source.put(item(1, 2))
    await source.close()
    assert [i.ref.id async for i in source.items()] == ["tick-1", "tick-2"]


async def test_a_live_source_refuses_anything_but_items() -> None:
    source = LiveSource()
    await source._queue.put("not an item")
    with pytest.raises(TypeError, match="carries items"):
        async for _ in source.items():
            pass
