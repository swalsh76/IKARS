"""Recording the live feed: typed tables per channel, one file per channel per hour (PLAN D4 C).

The open hour is an Arrow IPC stream (``<channel>/<YYYY-MM-DD>/<HH>.arrow``), appended in batches
and flushed every few seconds, so a hard crash loses at most the last unflushed seconds (and the gap
log says so). When the hour is over, or at the next start after a crash, it is **compacted** to
Parquet (zstd) beside it: rows sorted by received time, trades de-duplicated by ``trade_id`` (the
live feed and a backfill may both deliver one), merged with any Parquet already written for that
hour (a restart in the middle of an hour), and the IPC file removed. A truncated IPC file (the crash
case) is read up to its last complete batch.

Money and counts are stored as the fixed-point integers the models give (dollar millionths,
contract hundredths), never floats; times as integers (nanoseconds received, Kalshi's milliseconds).
"""

import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

_I64 = pa.int64()
_STR = pa.string()

SCHEMAS: dict[str, pa.Schema] = {
    "trade": pa.schema(
        [
            ("received_ns", _I64),
            ("ts_ms", _I64),
            ("ticker", _STR),
            ("trade_id", _STR),
            ("yes_price_e6", _I64),
            ("no_price_e6", _I64),
            ("count_e2", _I64),
            ("taker_side", _STR),
            ("backfilled", pa.bool_()),
        ]
    ),  # fmt: skip
    "ticker": pa.schema(
        [
            ("received_ns", _I64),
            ("ts_ms", _I64),
            ("ticker", _STR),
            ("price_e6", _I64),
            ("yes_bid_e6", _I64),
            ("yes_ask_e6", _I64),
            ("yes_bid_size_e2", _I64),
            ("yes_ask_size_e2", _I64),
            ("volume_e2", _I64),
            ("open_interest_e2", _I64),
        ]
    ),  # fmt: skip
    "lifecycle": pa.schema(
        [
            ("received_ns", _I64),
            ("ticker", _STR),
            ("event_type", _STR),
            ("result", _STR),
            ("message", _STR),
        ]
    ),  # fmt: skip
    "book_snapshot": pa.schema(
        [
            ("received_ns", _I64),
            ("ticker", _STR),
            ("snapshot", _I64),
            ("side", _STR),
            ("price_e6", _I64),
            ("count_e2", _I64),
        ]
    ),  # fmt: skip
    "book_delta": pa.schema(
        [
            ("received_ns", _I64),
            ("ts_ms", _I64),
            ("ticker", _STR),
            ("seq", _I64),
            ("side", _STR),
            ("price_e6", _I64),
            ("delta_e2", _I64),
        ]
    ),  # fmt: skip
}
CHANNELS = tuple(SCHEMAS)


def hour_of(received_ns: int) -> tuple[str, str]:
    moment = datetime.fromtimestamp(received_ns // 1_000_000_000, UTC)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H")


class Recorder:
    def __init__(self, root: Path, *, flush_seconds: float = 5.0, batch_rows: int = 50_000) -> None:
        self.root = root
        self._flush_seconds = flush_seconds
        self._batch_rows = batch_rows
        self._rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._open: dict[
            str, tuple[tuple[str, str], Any, Path]
        ] = {}  # channel -> (hour, writer, path)
        self._last_flush = time.monotonic()
        self.rows_written: dict[str, int] = defaultdict(int)

    # --- writing -----------------------------------------------------------------------------

    def write(self, channel: str, row: dict[str, Any]) -> None:
        if channel not in SCHEMAS:
            raise ValueError(f"no such channel {channel!r}")
        rows = self._rows[channel]
        if rows and hour_of(rows[-1]["received_ns"]) != hour_of(row["received_ns"]):
            self._flush_channel(channel)  # never mix two hours in one batch
            rows = self._rows[channel]  # the flush took the old list away: append to the new one
        rows.append(row)
        if (
            len(rows) >= self._batch_rows
            or time.monotonic() - self._last_flush >= self._flush_seconds
        ):
            self.flush()

    def flush(self) -> None:
        for channel in list(self._rows):
            self._flush_channel(channel)
        self._last_flush = time.monotonic()

    def _flush_channel(self, channel: str) -> None:
        rows = self._rows.pop(channel, [])
        if not rows:
            return
        hour = hour_of(rows[0]["received_ns"])
        current = self._open.get(channel)
        if current is not None and current[0] != hour:
            self._close_channel(channel)
            current = None
        if current is None:
            path = self.root / channel / hour[0] / f"{hour[1]}.arrow"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():  # a crash left this hour open: compact it before starting afresh
                compact(path, channel)
            sink = pa.OSFile(str(path), "wb")
            writer = ipc.new_stream(sink, SCHEMAS[channel])
            current = (hour, (writer, sink), path)
            self._open[channel] = current
        writer, sink = current[1]
        writer.write_batch(pa.RecordBatch.from_pylist(rows, schema=SCHEMAS[channel]))
        sink.flush()
        self.rows_written[channel] += len(rows)

    def _close_channel(self, channel: str) -> None:
        hour, (writer, sink), path = self._open.pop(channel)
        writer.close()
        sink.close()
        compact(path, channel)

    def close(self) -> None:
        """Flush everything and compact every open hour."""
        self.flush()
        for channel in list(self._open):
            self._close_channel(channel)

    def known_trade_ids(self, start_ns: int, end_ns: int) -> set[str]:
        """Every ``trade_id`` already recorded with a received time in the hours spanning the window
        (compacted, open or still buffered), so a backfill stores only what the feed missed."""
        self._flush_channel("trade")
        found: set[str] = set()
        hour = start_ns - start_ns % HOUR_NS_
        while hour <= end_ns:
            day, hh = hour_of(hour)
            folder = self.root / "trade" / day
            compacted, open_hour = folder / f"{hh}.parquet", folder / f"{hh}.arrow"
            if compacted.exists():
                found.update(pq.read_table(compacted, columns=["trade_id"])["trade_id"].to_pylist())
            if open_hour.exists():  # the current hour, flushed just above
                found.update(_read_stream(open_hour, SCHEMAS["trade"])["trade_id"].to_pylist())
            hour += HOUR_NS_
        return found

    def recover(self) -> int:
        """Compact every IPC file a previous run left behind (its hour may be over)."""
        found = sorted(self.root.glob("*/*/*.arrow"))
        for path in found:
            compact(path, path.parent.parent.name)
        return len(found)


HOUR_NS_ = 3_600 * 1_000_000_000


def _read_stream(path: Path, schema: pa.Schema) -> pa.Table:
    """Every complete batch of an IPC stream; a torn tail (a crash) is left out."""
    batches: list[pa.RecordBatch] = []
    try:
        with pa.OSFile(str(path), "rb") as source:
            reader = ipc.open_stream(source)
            while True:
                try:
                    batches.append(reader.read_next_batch())
                except StopIteration:
                    break
    except (pa.ArrowInvalid, OSError):
        pass  # truncated: keep what was read
    return pa.Table.from_batches(batches, schema=schema)


def compact(path: Path, channel: str) -> Path:
    """Fold the IPC file at ``path`` into the hour's Parquet file, then remove it."""
    schema = SCHEMAS[channel]
    target = path.with_suffix(".parquet")
    tables = [_read_stream(path, schema)]
    if target.exists():
        tables.insert(0, pq.read_table(target, schema=schema))
    table = pa.concat_tables(tables)
    table = table.take(pc.sort_indices(table, sort_keys=[("received_ns", "ascending")]))
    if channel == "trade" and table.num_rows:
        table = _first_of_each(table, "trade_id")
    temporary = target.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(target)
    path.unlink()
    return target


def _first_of_each(table: pa.Table, key: str) -> pa.Table:
    """Keep the first row (by received time; the table is sorted) of each ``key``."""
    seen: set[str] = set()
    keep: list[int] = []
    for index, value in enumerate(table.column(key).to_pylist()):
        if value not in seen:
            seen.add(value)
            keep.append(index)
    return table.take(pa.array(keep, pa.int64())) if len(keep) != table.num_rows else table


def hours(root: Path, channel: str) -> list[Path]:
    """Every compacted hour of ``channel``, oldest first."""
    return sorted((root / channel).glob("*/*.parquet"))


def read_rows(paths: Iterable[Path], channel: str) -> pa.Table:
    tables = [pq.read_table(p, schema=SCHEMAS[channel]) for p in paths]
    return pa.concat_tables(tables) if tables else SCHEMAS[channel].empty_table()
