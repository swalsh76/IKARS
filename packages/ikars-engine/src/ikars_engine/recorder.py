"""Recording the live feed: typed tables per channel, one file per channel per hour (PLAN D4 C).

Each hour is an Arrow IPC stream while it is open (``<channel>/<YYYY-MM-DD>/<HH>.arrow``), appended
in batches and flushed every few seconds, so a hard crash loses at most the last unflushed seconds
(and the gap log says so). An hour stays open for a grace period after it ends (two minutes), so
rows still buffered for it land in its own file; a row later still (a pump far behind) goes to the
first open hour, as a closed hour is never reopened. Then it is closed and queued for compaction,
which the caller runs off the event loop (``compact_pending`` in a worker thread): the hour becomes
Parquet (zstd), rows sorted by received time, trades de-duplicated by ``trade_id``, merged with any
Parquet already written for that hour.

Compaction is a two-step commit that a crash can never turn into duplicates: the merged Parquet is
written to a temporary file, the IPC file is renamed ``.merged`` (from then on its rows are in the
temporary file), the temporary file replaces the hour's Parquet, and the ``.merged`` file goes.
``recover`` at the next start finishes whichever step was interrupted, and compacts any hour a
crash left open (a torn last batch is read up to the last complete one). A compaction that fails
(a file held by another program) is logged and left for later; it never stops the recording or a
start. An hour whose earlier file could not be folded in is never overwritten: new rows go to a part
file beside it (``<HH>.1.arrow``, compacted to ``<HH>.1.parquet``), and readers take every part. A
compacted file that cannot be read (a crash or a bad disk) is moved aside as ``.damaged``, never
deleted, and logged; the hour's other rows are kept (M1.R 3 and 5).

Money and counts are stored as the fixed-point integers the models give (dollar millionths,
contract hundredths), never floats; times as integers (nanoseconds received, Kalshi's milliseconds).
"""

import logging
import os
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

log = logging.getLogger("ikars_engine.recorder")
_I64 = pa.int64()
_STR = pa.string()
HOUR_NS = 3_600 * 1_000_000_000
GRACE_NS = 120 * 1_000_000_000  # an hour stays open this long after it ends, for late frames

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
Hour = tuple[str, str]  # (YYYY-MM-DD, HH)


def hour_of(received_ns: int) -> Hour:
    moment = datetime.fromtimestamp(received_ns // 1_000_000_000, UTC)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H")


def hour_start_ns(hour: Hour) -> int:
    moment = datetime.strptime(f"{hour[0]} {hour[1]}", "%Y-%m-%d %H").replace(tzinfo=UTC)
    return int(moment.timestamp()) * 1_000_000_000


class Recorder:
    def __init__(self, root: Path, *, flush_seconds: float = 5.0, batch_rows: int = 50_000) -> None:
        self.root = root
        self._flush_seconds = flush_seconds
        self._batch_rows = batch_rows
        self._rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._open: dict[tuple[str, Hour], tuple[Any, Any, Path]] = {}  # -> (writer, sink, path)
        self._pending: list[tuple[Path, str]] = []  # closed hours waiting for compaction
        self._closed_through: dict[str, int] = {}  # channel -> start of its newest closed hour
        self.late_rows = 0
        self._last_flush = time.monotonic()
        self.rows_written: dict[str, int] = defaultdict(int)
        self.compaction_failures = 0

    # --- writing -----------------------------------------------------------------------------

    def write(self, channel: str, row: dict[str, Any]) -> None:
        if channel not in SCHEMAS:
            raise ValueError(f"no such channel {channel!r}")
        rows = self._rows[channel]
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
        by_hour: dict[Hour, list[dict[str, Any]]] = defaultdict(list)
        closed = self._closed_through.get(channel)
        for row in rows:
            at = row["received_ns"]
            if closed is not None and at < closed + HOUR_NS:
                # its hour is closed (and may be compacting in a worker): never reopened; the row
                # goes to the first open hour (a reader sorts by received time)
                at = closed + HOUR_NS
                self.late_rows += 1
            by_hour[hour_of(at)].append(row)
        for hour, batch in sorted(by_hour.items()):
            writer, sink, _ = self._writer(channel, hour)
            writer.write_batch(pa.RecordBatch.from_pylist(batch, schema=SCHEMAS[channel]))
            sink.flush()
            self.rows_written[channel] += len(batch)

    def _writer(self, channel: str, hour: Hour) -> tuple[Any, Any, Path]:
        key = (channel, hour)
        if key not in self._open:
            folder = self.root / channel / hour[0]
            path = folder / f"{hour[1]}.arrow"
            folder.mkdir(parents=True, exist_ok=True)
            if path.exists():  # this hour was open before (a restart within the hour): fold it in
                self._compact_now(path, channel)
            part = 0
            while path.exists():  # it could not be folded in: never overwrite it, write beside it
                part += 1
                path = folder / f"{hour[1]}.{part}.arrow"
            sink = pa.OSFile(str(path), "wb")
            self._open[key] = (ipc.new_stream(sink, SCHEMAS[channel]), sink, path)
        return self._open[key]

    def close_finished(self, now_ns: int) -> int:
        """Close every hour that ended more than the grace period ago and queue it for compaction;
        returns how many were closed. Cheap: the compaction itself is ``compact_pending``."""
        self.flush()
        closed = 0
        for (channel, hour), (writer, sink, path) in list(self._open.items()):
            start = hour_start_ns(hour)
            if start + HOUR_NS + GRACE_NS <= now_ns:
                writer.close()
                sink.close()
                del self._open[(channel, hour)]
                self._closed_through[channel] = max(self._closed_through.get(channel, start), start)
                self._pending.append((path, channel))
                closed += 1
        return closed

    def compact_pending(self) -> int:
        """Compact every queued hour (run this in a worker thread); returns how many succeeded."""
        done = 0
        while self._pending:
            path, channel = self._pending.pop(0)
            done += self._compact_now(path, channel)
        return done

    def _compact_now(self, path: Path, channel: str) -> int:
        try:
            compact(path, channel)
            return 1
        except Exception as exc:  # held by another program, or worse: left for later, never fatal
            self.compaction_failures += 1
            log.warning(
                "compaction failed; left for later", extra={"file": path.name, "error": repr(exc)}
            )
            return 0

    def close(self) -> None:
        """Flush everything, close every open hour and compact it (at shutdown)."""
        self.flush()
        for (channel, _hour), (writer, sink, path) in list(self._open.items()):
            writer.close()
            sink.close()
            self._pending.append((path, channel))
        self._open.clear()
        self.compact_pending()

    def recover(self) -> int:
        """At start: finish any compaction a crash interrupted, then compact every hour a previous
        run left open. Returns how many files were dealt with."""
        count = 0
        for merged in sorted(self.root.glob("*/*/*.arrow.merged")):
            try:
                finish_compaction(merged)
                count += 1
            except OSError as exc:  # held: finished at a later start
                self.compaction_failures += 1
                log.warning(
                    "compaction not finished", extra={"file": merged.name, "error": repr(exc)}
                )
        for path in sorted(self.root.glob("*/*/*.arrow")):
            count += self._compact_now(path, path.parent.parent.name)
        return count

    def known_trade_ids(self, start_ns: int, end_ns: int) -> set[str]:
        """Every ``trade_id`` already recorded with a received time in the hours spanning the window
        (compacted, open or still buffered), so a backfill stores only what the feed missed."""
        self._flush_channel("trade")
        found: set[str] = set()
        hour = start_ns - start_ns % HOUR_NS
        while hour <= end_ns:
            day, hh = hour_of(hour)
            folder = self.root / "trade" / day
            # a compaction may be running in a worker thread: its rows are in one of these; an
            # hour can have part files (HH.1.parquet, ...) beside the first
            for compacted in folder.glob(f"{hh}.*parquet"):
                try:
                    table = pq.read_table(compacted, columns=["trade_id"])
                    found.update(table["trade_id"].to_pylist())
                except (pa.ArrowInvalid, OSError):
                    pass  # being replaced this instant; the stream it came from is read below
            for stream in [*folder.glob(f"{hh}.*arrow"), *folder.glob(f"{hh}.*arrow.merged")]:
                found.update(_read_stream(stream, SCHEMAS["trade"])["trade_id"].to_pylist())
            hour += HOUR_NS
        return found


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
    """Fold the IPC file at ``path`` into the hour's Parquet file, in two steps a crash cannot
    turn into duplicates (see the module's notes)."""
    schema = SCHEMAS[channel]
    target = path.with_suffix(".parquet")
    temporary = path.with_suffix(".parquet.tmp")
    tables = [_read_stream(path, schema)]
    if target.exists():
        try:
            tables.insert(0, pq.read_table(target, schema=schema))
        except pa.ArrowInvalid as exc:  # not a readable Parquet file: kept aside, never deleted
            damaged = _free_name(target.with_name(target.name + ".damaged"))
            target.replace(damaged)
            log.error(
                "a recorded hour could not be read; set aside",
                extra={"file": target.name, "kept_as": damaged.name, "error": repr(exc)},
            )
    table = pa.concat_tables(tables)
    table = table.take(pc.sort_indices(table, sort_keys=[("received_ns", "ascending")]))
    if channel == "trade" and table.num_rows:
        table = _first_of_each(table, "trade_id")
    pq.write_table(table, temporary, compression="zstd")
    with open(temporary, "rb+") as written:  # on disk before it replaces anything (a power cut)
        os.fsync(written.fileno())
    merged = path.with_suffix(".arrow.merged")
    path.replace(merged)  # step 1: from here the rows live in the temporary file
    return finish_compaction(merged)


def _free_name(path: Path) -> Path:
    """``path``, or ``path.1``, ``path.2``, ... the first that does not exist."""
    candidate, n = path, 0
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}.{n}")
    return candidate


def finish_compaction(merged: Path) -> Path:
    """Step 2 (also the recovery of an interrupted step 2)."""
    target = merged.with_name(merged.name.removesuffix(".arrow.merged") + ".parquet")
    temporary = target.with_suffix(".parquet.tmp")
    if temporary.exists():
        temporary.replace(target)
    merged.unlink()
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
