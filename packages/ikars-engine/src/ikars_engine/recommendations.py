"""The recommendations as the owner sees them (M1.12c; PLAN D20 D and H, G9).

- **Figures:** what a recommendation lays out (the contracts and the entry fee, which is also its
  worst case), its return on that at the rule's target after fees, and that return per day of the
  time it ties the money up (D20 D). All integers: a return in millionths of the outlay.
- **The board:** the active recommendations ranked by return per day, kept as they come and go
  (one per strategy and market, as the journal keeps them), so neither the Overview's list nor a
  notification ranks the whole journal.
- **The scorecard:** each strategy version's checks over the last 30 days, read from the journal's
  kept scores on a worker thread with its own read-only connection (about 10,000 recommendations
  a day: never on the event loop, never per request).
- **Notifications** (owner, 2026-10-28): only a recommendation that ranks first when it is made,
  at most three an hour and none in quiet hours (it would be stale by morning); the rest are on
  the Overview only, never in the alert log. Shown on a worker thread: a toast takes a second.
"""

import contextlib
import heapq
import logging
import sqlite3
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ikars_core.fees import FeeSchedule
from ikars_core.fixedpoint import E6

from ikars_engine.alerts import NORMAL, Alert, AlertDesk, NotificationFailed
from ikars_engine.pnl import outlay_e6

if TYPE_CHECKING:
    from ikars_engine.journal import Recommendation

log = logging.getLogger("ikars_engine.recommendations")
DAY_NS = 86_400 * 1_000_000_000
LIMIT_E6 = 10**15  # a return per day is held to this (exact in JavaScript; a tiny validity can't
# make it absurd)
TOP = 50  # the most the Overview lists
BUDGET = 3  # notifications an hour (owner, 2026-10-28)
CARD_DAYS = 30
ENGINE_DESK = "alert-desk-engine.json"  # the engine's alert desk state (the watchdog keeps its own)


@dataclass(frozen=True, slots=True)
class Figures:
    outlay_e6: int  # the contracts and the entry fee: also the worst case
    return_e6: int | None  # at the target after both fees, in millionths of the outlay
    per_day_e6: int | None  # that, per day of the recommendation's validity


def figures(rec: "Recommendation", made_ns: int, fees: FeeSchedule | None) -> Figures | None:
    """None without a fee schedule (nothing can be said after costs)."""
    if fees is None:
        return None
    outlay = outlay_e6(fees, rec.count_e2, rec.entry_price_e6)
    if rec.pnl_e6 is None:
        return Figures(outlay, None, None)
    ratio = (
        rec.pnl_e6 * E6 // outlay
    )  # outlay > 0: a price of at least a millionth, whole contracts
    days = max(1, rec.expires_ns - made_ns)
    per_day = max(-LIMIT_E6, min(LIMIT_E6, ratio * DAY_NS // days))
    return Figures(outlay, ratio, per_day)


@dataclass
class Board:
    """The active recommendations' ranking, by return per day (none last)."""

    # (strategy, ticker) -> (id, expires_ns, return per day)
    places: dict[tuple[str, str], tuple[str, int, int | None]] = field(default_factory=dict)
    hidden: set[str] = field(default_factory=set)  # dismissed by the owner

    def put(self, rid: str, rec: "Recommendation", per_day: int | None, made_ns: int) -> bool:
        """Places ``rid`` (replacing the strategy's earlier one on that market); whether it ranks
        first among the shown ones still valid when it was made (merge review 7)."""
        key = (rec.strategy, rec.ticker)
        others = [p for k, (i, expires, p) in self.places.items()
                  if k != key and i not in self.hidden and p is not None
                  and expires > made_ns]  # fmt: skip
        replaced = self.places.get(key)
        if replaced is not None:  # its dismissal goes with it (merge review 8)
            self.hidden.discard(replaced[0])
        self.places[key] = (rid, rec.expires_ns, per_day)
        return per_day is not None and all(per_day > p for p in others)

    def prune(self, now_ns: int) -> None:
        gone = [k for k, (_, expires, _) in self.places.items() if expires <= now_ns]
        for key in gone:
            self.hidden.discard(self.places.pop(key)[0])

    def hide(self, rid: str, hidden: bool) -> None:
        """Only one on the board is kept hidden: an expired or replaced one is gone (review 8)."""
        if hidden and any(i == rid for i, _, _ in self.places.values()):
            self.hidden.add(rid)
        else:
            self.hidden.discard(rid)

    def shown(self) -> int:
        return sum(i not in self.hidden for i, _, _ in self.places.values())

    def top(self, count: int = TOP) -> list[str]:
        ranked = heapq.nsmallest(
            count,
            ((p is None, -(p or 0), rid) for rid, _, p in self.places.values()
             if rid not in self.hidden),
        )  # fmt: skip
        return [rid for _, _, rid in ranked]


@dataclass(frozen=True, slots=True)
class ScoreLine:
    strategy: str
    version: str
    horizon_s: int
    checks: int  # made in the period (scored, unscorable and waiting for a settlement)
    scored: int
    above_zero: int
    mean_e6: int | None
    median_e6: int | None


def scorecard(path: Path, since_ns: int) -> list[ScoreLine]:
    """Every strategy version's checks of recommendations made since ``since_ns``, from the
    journal's kept scores (a read-only connection: call it on a worker thread)."""
    uri = path.resolve().as_uri() + "?mode=ro"
    lines = []
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        groups = db.execute(
            "SELECT r.strategy, r.version, m.horizon_s, COUNT(*), COUNT(m.score_e6),"
            " SUM(m.score_e6 > 0), SUM(m.score_e6) FROM recommendation r JOIN mark m ON m.id = r.id"
            " WHERE r.received_ns >= ? GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
            (since_ns,),
        ).fetchall()
        for strategy, version, horizon, checks, scored, above, total in groups:
            mean = median = None
            if scored:
                mean = total // scored
                middle = [v for (v,) in db.execute(
                    "SELECT m.score_e6 FROM recommendation r JOIN mark m ON m.id = r.id"
                    " WHERE r.strategy = ? AND r.version = ? AND m.horizon_s = ?"
                    " AND r.received_ns >= ? AND m.score_e6 IS NOT NULL"
                    " ORDER BY m.score_e6 LIMIT ? OFFSET ?",
                    (strategy, version, horizon, since_ns, 2 - scored % 2, (scored - 1) // 2),
                )]  # fmt: skip
                median = sum(middle) // len(middle)
            lines.append(ScoreLine(strategy, version, horizon, checks, scored, above or 0, mean,
                                   median))  # fmt: skip
    return lines


def cents_text(price_e6: int) -> str:
    whole, part = divmod(price_e6, 10_000)
    return f"{whole}c" if not part else f"{whole}.{part:04d}".rstrip("0") + "c"


class Notices:
    """Notifications for the strongest recommendations (owner, 2026-10-28)."""

    def __init__(
        self,
        desk: AlertDesk,
        title: Callable[[str], str],  # a market's title by ticker ("" when not known)
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        budget: int = BUDGET,
    ) -> None:
        self._desk = desk
        self._title = title
        self._now = now
        self._budget = budget
        self._sent: deque[datetime] = deque()
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="notices")

    def offer(self, rid: str, rec: "Recommendation") -> bool:
        """``rec`` ranks first: notify unless it is quiet or the hour's budget is spent. Keyed by
        the recommendation, so the desk's throttle never spends the budget on nothing (review 5)."""
        now = self._now()
        if self._desk.quiet(now):
            return False
        while self._sent and self._sent[0] <= now - timedelta(hours=1):
            self._sent.popleft()
        if len(self._sent) >= self._budget:
            return False
        title = self._title(rec.ticker) or rec.ticker
        alert = Alert(f"recommendation:{rid}", NORMAL,
                      f"Buy {rec.side.upper()} at {cents_text(rec.entry_price_e6)}",
                      f"{title}: {rec.summary}")  # fmt: skip
        try:
            self._pool.submit(self._show, alert, now)
        except RuntimeError:  # closed (the engine is stopping): stale by the next start (review 1)
            return False
        self._sent.append(now)
        return True

    def _show(self, alert: Alert, now: datetime) -> None:
        try:
            self._desk.raise_alert(alert, now)
        except (NotificationFailed, OSError) as exc:  # in the log as failed; never retried: stale
            log.warning("a recommendation notification failed", extra={"error": str(exc)})

    def close(self, wait: bool = False) -> None:
        """At stop a waiting notification is let go (stale by the next start)."""
        self._pool.shutdown(wait=wait, cancel_futures=not wait)
