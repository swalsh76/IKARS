"""Recordings, the replay and the point-in-time rule (PLAN D20 A)."""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ikars_engine.clocks import ClockWentBackwards, VirtualClock, WallClock
from ikars_engine.model import Item, Link, Ref, decode_item, encode
from ikars_engine.sources import LiveSource, ReplaySource, read_recordings, write_recording


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


def test_recordings_merge_in_received_order_keeping_ties_in_file_order(tmp_path: Path) -> None:
    write_recording(tmp_path / "a.parquet", [item(30, 1), item(10, 2), item(20, 3)])
    write_recording(tmp_path / "b.parquet", [item(20, 4), item(5, 5)])
    table = read_recordings([tmp_path / "a.parquet", tmp_path / "b.parquet"])
    assert table.column("received_ns").to_pylist() == [5, 10, 20, 20, 30]
    assert table.column("id").to_pylist() == ["tick-5", "tick-2", "tick-3", "tick-4", "tick-1"]
    assert read_recordings([]).num_rows == 0


async def test_the_replay_never_releases_an_item_before_its_received_time(tmp_path: Path) -> None:
    write_recording(tmp_path / "r.parquet", [item(at, n) for n, at in enumerate([3, 1, 2, 2, 9])])
    clock = VirtualClock()
    seen = []
    async for released in ReplaySource([tmp_path / "r.parquet"], clock).items():
        assert clock.now_ns() == released.received_ns  # time reached the item first
        seen.append(released.received_ns)
    assert seen == sorted(seen) == [1, 2, 2, 3, 9]


@settings(max_examples=25, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=10**15), min_size=1, max_size=40))
def test_replay_order_is_received_order_for_any_recording(times: list[int]) -> None:
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "r.parquet"
        write_recording(path, [item(at, n) for n, at in enumerate(times)])
        clock = VirtualClock()

        async def collect() -> list[int]:
            return [i.received_ns async for i in ReplaySource([path], clock).items()]

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
