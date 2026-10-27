"""One input interface, live or replayed (PLAN D19 G3, D20 A).

The engine reads ``Item``s from a ``Source`` and never knows which kind it has:

- ``LiveSource``: feeds and plugins put items into it as they arrive (stamped with the wall
  clock's received time); the engine takes them in arrival order.
- ``ReplaySource``: reads recordings (Parquet, ``RECORDING_SCHEMA``), merges them in received-time
  order, and releases each item only after moving the virtual clock to that item's received time,
  so a replay can never show anything before IKARS had it. Ties keep file and row order, so a
  replay is deterministic.

M1.4b's recorder writes recordings in this schema; ``write_recording`` is the one writer.
"""

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import Protocol

import msgspec
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from ikars_engine.clocks import VirtualClock
from ikars_engine.model import Item, Ref

RECORDING_SCHEMA = pa.schema(
    [
        pa.field("received_ns", pa.int64(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("kind", pa.string(), nullable=False),
        pa.field("id", pa.string(), nullable=False),
        pa.field("published_ns", pa.int64()),
        pa.field("payload", pa.binary(), nullable=False),  # the item's payload as JSON
    ]
)

_payload = msgspec.json.Encoder()
_payload_decoder = msgspec.json.Decoder(dict)


class Source(Protocol):
    def items(self) -> AsyncIterator[Item]: ...


def write_recording(path: Path, items: Iterable[Item]) -> int:
    """Write ``items`` to one Parquet recording (a temporary name, then renamed); returns how many
    were written."""
    rows = list(items)
    table = pa.table(
        {
            "received_ns": [i.received_ns for i in rows],
            "source": [i.source for i in rows],
            "kind": [i.ref.kind for i in rows],
            "id": [i.ref.id for i in rows],
            "published_ns": [i.published_ns for i in rows],
            "payload": [_payload.encode(i.payload) for i in rows],
        },
        schema=RECORDING_SCHEMA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)
    return len(rows)


def read_recordings(paths: Sequence[Path]) -> pa.Table:
    """Every row of ``paths`` in received-time order; ties keep file order, then row order."""
    tables = []
    for index, path in enumerate(paths):
        table = pq.read_table(path, schema=RECORDING_SCHEMA)
        tables.append(table.append_column("_file", pa.array([index] * table.num_rows, pa.int32())))
    if not tables:
        return RECORDING_SCHEMA.empty_table()
    merged = pa.concat_tables(tables)
    merged = merged.append_column("_row", pa.array(range(merged.num_rows), pa.int64()))
    order = pc.sort_indices(
        merged,
        sort_keys=[("received_ns", "ascending"), ("_file", "ascending"), ("_row", "ascending")],
    )
    return merged.take(order).drop_columns(["_file", "_row"])


class ReplaySource:
    def __init__(self, paths: Sequence[Path], clock: VirtualClock) -> None:
        self._paths = list(paths)
        self._clock = clock

    async def items(self) -> AsyncIterator[Item]:
        table = read_recordings(self._paths)
        columns = table.to_pydict()
        for at, source, kind, id_, published, payload in zip(
            columns["received_ns"],
            columns["source"],
            columns["kind"],
            columns["id"],
            columns["published_ns"],
            columns["payload"],
            strict=True,
        ):
            self._clock.advance_to(at)  # time reaches the item before the item is released
            yield Item(
                ref=Ref(kind, id_),
                source=source,
                received_ns=at,
                published_ns=published,
                payload=_payload_decoder.decode(payload),
            )
            await asyncio.sleep(0)


class LiveSource:
    """Items pushed by live feeds and plugins, taken in arrival order until ``close``."""

    _END = object()

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize)

    async def put(self, item: Item) -> None:
        await self._queue.put(item)

    async def close(self) -> None:
        await self._queue.put(self._END)

    async def items(self) -> AsyncIterator[Item]:
        while True:
            entry = await self._queue.get()
            if entry is self._END:
                return
            if not isinstance(entry, Item):
                raise TypeError(f"a live source carries items, not {type(entry).__name__}")
            yield entry
