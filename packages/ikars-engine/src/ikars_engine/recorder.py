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
compacted file that cannot be read (a crash or a bad disk) is moved aside as ``.damaged`` (or
``.damaged.1`` and on) and logged, never overwritten; the hour's other rows are kept (M1.R 3 and 5).
Like any hour it counts against the storage budget, and the governor prunes it oldest first.

Money and counts are stored as the fixed-point integers the models give (dollar millionths,
contract hundredths), never floats; times as integers (nanoseconds received, Kalshi's milliseconds).

**Columns are only ever added** (M1.13: the books' ``clock``, the snapshots' ``checkpoint``). A file
written before a column existed is read with that column null (``conform``), so an hour that spans
an upgrade still compacts, and a replay reads every recording.
"""

import logging
import os
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

import msgspec
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

from ikars_engine.files import first_free
from ikars_engine.model import Item, Ref

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
    # one row per level; an empty book is one row with no side (M1.13), so a replay sees it too
    "book_snapshot": pa.schema(
        [
            ("received_ns", _I64),
            ("ticker", _STR),
            ("snapshot", _I64),
            ("side", _STR),
            ("price_e6", _I64),
            ("count_e2", _I64),
            ("clock", _I64),  # Kalshi's, when it sends one (none in production on 2026-10-26)
            ("checkpoint", pa.bool_()),  # the hourly copy of a kept book, not a feed message
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
            ("clock", _I64),
        ]
    ),  # fmt: skip
    # a market's details when they first appear or change, and every market after each full
    # discovery (``full``), so a replay knows them as they were (M1.13; owner, 2026-11-01)
    "market": pa.schema(
        [
            ("received_ns", _I64),
            ("ticker", _STR),
            ("event_ticker", _STR),
            ("series_ticker", _STR),
            ("category", _STR),
            ("title", _STR),
            ("status", _STR),
            ("close_ns", _I64),
            ("result", _STR),
            ("settlement_value_e6", _I64),  # what a YES contract paid (null before M1.14's fix)
            ("climate_code", _STR),
            ("fee_type", _STR),  # the series' fee terms then
            ("fee_multiplier_e6", _I64),
            ("full", pa.bool_()),
            ("removed", pa.bool_()),  # the index dropped it (long settled)
        ]
    ),  # fmt: skip
    # what the live feed did to its kept state: a start, a reconnect (every book cleared), a
    # sequence gap (the books broken until their snapshots), so a replay does the same (M1.13)
    "feed": pa.schema(
        [
            ("received_ns", _I64),
            ("event", _STR),
            ("channel", _STR),
            ("detail", _STR),
        ]
    ),  # fmt: skip
}
# what plugins emit (M1.12a; D20 A's generic recording schema): every item, its payload as JSON
SCHEMAS["item"] = pa.schema(
    [
        ("received_ns", _I64),  # the host's clock (review 1.R2 item 9), never the plugin's
        ("published_ns", _I64),
        ("kind", _STR),
        ("id", _STR),
        ("source", _STR),
        ("hops", _I64),
        ("payload", _STR),
    ]
)  # fmt: skip
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


def conform(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """``table`` in ``schema``'s columns and order; a column an older recording lacks is null."""
    if table.schema.equals(schema):
        return table
    columns = [
        table.column(f.name) if f.name in table.column_names else pa.nulls(table.num_rows, f.type)
        for f in schema
    ]
    return pa.Table.from_arrays(columns, schema=schema)


def _read_stream(path: Path, schema: pa.Schema) -> pa.Table:
    """Every complete batch of an IPC stream; a torn tail (a crash) is left out. A stream written
    before a column existed is read with it null (M1.13)."""
    batches: list[pa.RecordBatch] = []
    written = schema
    try:
        with pa.OSFile(str(path), "rb") as source:
            reader = ipc.open_stream(source)
            written = reader.schema
            while True:
                try:
                    batches.append(reader.read_next_batch())
                except StopIteration:
                    break
    except (pa.ArrowInvalid, OSError):
        pass  # truncated: keep what was read
    return conform(pa.Table.from_batches(batches, schema=written), schema)


def compact(path: Path, channel: str) -> Path:
    """Fold the IPC file at ``path`` into the hour's Parquet file, in two steps a crash cannot
    turn into duplicates (see the module's notes)."""
    schema = SCHEMAS[channel]
    target = path.with_suffix(".parquet")
    temporary = path.with_suffix(".parquet.tmp")
    merged = path.with_suffix(".arrow.merged")
    if merged.exists():
        # an earlier fold-in of this hour stopped between its steps (its Parquet was held): finish
        # it first, or this one would overwrite the rows it still holds (M1.R merge review 1)
        finish_compaction(merged)
    tables = [_read_stream(path, schema)]
    if target.exists():
        try:
            tables.insert(0, pq.read_table(target, schema=schema))
        except pa.ArrowInvalid as exc:
            if _readable(target):
                raise  # a sound file that differs from the schema: never set aside, left for later
            # not a readable Parquet file: kept aside, never deleted
            damaged = first_free(
                target.with_name(target.name + ".damaged" + ("" if n == 0 else f".{n}"))
                for n in count()
            )
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
    path.replace(merged)  # step 1: from here the rows live in the temporary file
    return finish_compaction(merged)


def _readable(path: Path) -> bool:
    """Whether ``path`` is a Parquet file whose footer can be read (whatever its schema)."""
    try:
        return pq.ParquetFile(path).metadata is not None
    except (pa.ArrowInvalid, OSError):
        return False


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


def item_row(item: Item) -> dict[str, Any]:
    """An item a plugin emitted, as the ``item`` channel records it (D20 A)."""
    return {
        "received_ns": item.received_ns, "published_ns": item.published_ns,
        "kind": item.ref.kind, "id": item.ref.id, "source": item.source, "hops": item.hops,
        "payload": msgspec.json.encode(item.payload).decode(),
    }  # fmt: skip


def item_from_row(row: dict[str, Any]) -> Item:
    """``item_row``'s inverse: the item as it was emitted (M1.13)."""
    return Item(
        ref=Ref(row["kind"], row["id"]), source=row["source"], received_ns=row["received_ns"],
        published_ns=row["published_ns"], payload=msgspec.json.decode(row["payload"]),
        hops=row["hops"] or 0,
    )  # fmt: skip


def write_items(root: Path, items: Iterable[Item]) -> int:
    """A made-up recording of ``items`` in the ``item`` channel, compacted (the smoke run and
    tests); returns how many were written."""
    recorder = Recorder(root)
    count = 0
    for item in items:
        recorder.write("item", item_row(item))
        count += 1
    recorder.close()
    return count


def hours(root: Path, channel: str) -> list[Path]:
    """Every compacted hour of ``channel``, oldest first."""
    return sorted((root / channel).glob("*/*.parquet"))


def read_rows(paths: Iterable[Path], channel: str) -> pa.Table:
    tables = [pq.read_table(p, schema=SCHEMAS[channel]) for p in paths]
    return pa.concat_tables(tables) if tables else SCHEMAS[channel].empty_table()


def hour_files(root: Path, channel: str, hour: Hour) -> list[Path]:
    """Every file holding ``channel``'s rows for ``hour``: its Parquet, its part files, and an
    hour still open or folding in (``.arrow``, ``.arrow.merged``). Damaged files are left out."""
    folder = root / channel / hour[0]
    if not folder.is_dir():
        return []
    names = (f"{hour[1]}.parquet", f"{hour[1]}.arrow", f"{hour[1]}.arrow.merged")
    found = [p for p in folder.iterdir() if p.name in names or _is_part(p.name, hour[1])]
    return sorted(found, key=lambda p: p.name)


def _is_part(name: str, hh: str) -> bool:
    head, _, rest = name.partition(".")
    number, _, kind = rest.partition(".")
    return head == hh and number.isdigit() and kind in ("parquet", "arrow", "arrow.merged")


def read_hour(root: Path, channel: str, hour: Hour, attempts: int = 5) -> pa.Table:
    """``channel``'s rows for ``hour`` from all its files, sorted by received time (stably: ties
    keep file order, then row order). An hour being compacted while it is read (the live engine
    folds a stream into its Parquet) could be read twice or not at all, so the files are listed
    before and after, and read again when they changed (per-commit review). A recording is only
    ever read, never changed."""
    schema = SCHEMAS[channel]
    tables: list[pa.Table] = []
    for _ in range(attempts):
        before = _listing(root, channel, hour)
        tables = []
        for path in hour_files(root, channel, hour):
            if path.name.endswith(".parquet"):
                try:
                    tables.append(pq.read_table(path, schema=schema))
                except (pa.ArrowInvalid, OSError):
                    continue  # being replaced, or damaged (the governor and compaction report it)
            else:
                tables.append(_read_stream(path, schema))
        if _listing(root, channel, hour) == before:
            break
        time.sleep(0.2)  # a compaction takes about a second: read again once it is done
    if not tables:
        return schema.empty_table()
    table = pa.concat_tables(tables)
    if len(tables) > 1 or not in_order(tables[0]):
        table = table.take(pc.sort_indices(table, sort_keys=[("received_ns", "ascending")]))
    return table


def _listing(root: Path, channel: str, hour: Hour) -> list[str]:
    """An hour's files by name: a compaction renames the stream it folds in (``.arrow`` to
    ``.arrow.merged`` to gone), while an open hour that only grows keeps its names (merge review
    5: appends are new rows, never rows read twice)."""
    return [path.name for path in hour_files(root, channel, hour)]


def iter_hour(
    root: Path, channel: str, hour: Hour, batch_rows: int = 65_536
) -> Iterator[pa.RecordBatch]:
    """``read_hour``'s rows in batches. A compacted hour (one sorted Parquet file, the usual case)
    is streamed a batch at a time, so a replay holds about one batch per channel, not the hour; an
    hour still open or in parts is read whole and sorted first."""
    files = hour_files(root, channel, hour)
    sent = 0
    if len(files) == 1 and files[0].name.endswith(".parquet"):
        try:
            source = pq.ParquetFile(files[0])
            if source.schema_arrow.equals(SCHEMAS[channel]):
                for batch in source.iter_batches(batch_size=batch_rows):
                    yield batch
                    sent += batch.num_rows
                return
        except (pa.ArrowInvalid, OSError):
            pass  # replaced this instant, or older columns: read whole below
    # whole, past what was already sent (a stream that failed part-way: merge review 2)
    yield from read_hour(root, channel, hour).slice(sent).to_batches(max_chunksize=batch_rows)


def in_order(table: pa.Table) -> bool:
    """Whether a table's rows are already in received-time order (a compacted hour is)."""
    column = table.column("received_ns")
    if column.null_count or table.num_rows < 2:
        return True
    return bool(pc.all(pc.greater_equal(column[1:], column[:-1])).as_py())
