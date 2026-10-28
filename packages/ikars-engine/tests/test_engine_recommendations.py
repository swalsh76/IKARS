"""M1.12c: the recommendations as the owner sees them: the figures, the ranking, the owner's
response, the kept scores and the scorecard, and the notifications."""

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import msgspec
import pytest
from ikars_core.fees import fee_schedule
from ikars_engine import pnl
from ikars_engine.alerts import ALERT_LOG, HIGH, Alert, AlertDesk, QuietHours
from ikars_engine.api import recommendation_view
from ikars_engine.journal import JOURNAL_MIGRATIONS, Journal, JournalService, Recommendation
from ikars_engine.model import Item, Ref
from ikars_engine.recommendations import (
    ENGINE_DESK,
    LIMIT_E6,
    Board,
    Figures,
    Notices,
    cents_text,
    figures,
    scorecard,
)
from ikars_engine.store import open_store

S = 1_000_000_000
T0 = 1_767_225_600 * S  # 2026-01-01 00:00 UTC
_fees = fee_schedule("quadratic", 1_000_000)
assert _fees is not None
FEES = _fees
DENVER = ZoneInfo("America/Denver")
NOON = datetime(2026, 1, 5, 12, 0, tzinfo=DENVER)
NIGHT = datetime(2026, 1, 5, 23, 30, tzinfo=DENVER)


def made(ticker: str = "KXA", pnl_e6: int | None = 40_000, valid_s: int = 1800) -> Recommendation:
    return Recommendation(
        ticker=ticker, side="yes", entry_price_e6=460_000, count_e2=1_000,
        expires_ns=T0 + valid_s * S, summary="made up: up 5c in 5 min", strategy="momentum",
        version="9f2ce1a0", horizons_s=[1800, 3600], inputs=[Ref("market", ticker)], pnl_e6=pnl_e6,
    )  # fmt: skip


def item(rid: str, rec: Recommendation, at: int = T0) -> Item:
    return Item(ref=Ref("recommendation", rid), source="momentum", received_ns=at,
                payload=msgspec.to_builtins(rec), hops=1)  # fmt: skip


# --- the figures and the ranking -----------------------------------------------------------------


def test_the_figures_by_hand() -> None:
    # 10 contracts at 46c: $4.60 and an 18c fee (7% of 10 x 0.46 x 0.54 = 17.4c, up to a cent);
    # $0.04 after both fees on $4.78 is 0.8368%; over half an hour, 48 times that a day
    assert pnl.outlay_e6(FEES, 1_000, 460_000) == 4_780_000
    assert figures(made(), T0, FEES) == Figures(4_780_000, 8_368, 401_664)
    assert figures(made(), T0, None) is None  # no fees: nothing after costs
    assert figures(made(pnl_e6=None), T0, FEES) == Figures(4_780_000, None, None)
    instant = made()
    found = figures(instant, instant.expires_ns - 1, FEES)  # valid for a nanosecond: held
    assert found is not None and found.per_day_e6 == LIMIT_E6
    loss = figures(made(pnl_e6=-360_000), T0, FEES)
    assert loss is not None and loss.return_e6 == -75_314  # rounded down, never up


def test_the_board_ranks_by_return_per_day() -> None:
    board = Board()
    assert board.put("a", made("KXA"), 100, T0) is True  # alone: first
    assert board.put("b", made("KXB"), 50, T0) is False
    assert board.put("c", made("KXC"), None, T0) is False  # nothing after costs: last, never first
    assert board.put("d", made("KXD"), 200, T0) is True
    assert board.top() == ["d", "a", "b", "c"] and board.top(2) == ["d", "a"]
    board.hide("d", True)  # dismissed
    assert board.top() == ["a", "b", "c"] and board.shown() == 3
    assert board.put("e", made("KXE"), 150, T0) is True  # first among those shown
    board.put("a2", made("KXA"), 10, T0)  # the strategy's newer one on KXA takes its place
    assert board.top() == ["e", "b", "a2", "c"]
    board.hide("d", False)
    assert board.top()[0] == "d"
    board.prune(T0 + 1800 * S)  # all expire
    assert board.top() == [] and board.hidden == set() and board.shown() == 0
    # merge review 7: one expired but not yet pruned does not outrank a new one
    assert board.put("old", made("KXA"), 900, T0) is True
    assert board.put("new", made("KXB"), 100, T0 + 1800 * S) is True
    # merge review 8: only one on the board stays hidden; a replaced one's dismissal goes with it
    board.hide("gone", True)
    board.hide("new", True)
    assert board.hidden == {"new"}
    board.put("newer", made("KXB"), 100, T0)
    assert board.hidden == set()


# --- the journal: responses, kept scores, the scorecard ------------------------------------------


def test_the_owner_s_response_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = Journal(path)
    journal.add(item("r1", made()))
    before = journal.changes
    assert journal.respond("r1", "taken", T0 + S) and journal.changes == before + 1
    found = journal.get("r1")
    assert found is not None and found.response == "taken"
    assert journal.respond("r1", "dismissed", T0 + 2 * S)
    assert journal.respond("nope", "taken", T0) is False
    with pytest.raises(ValueError, match="answer"):
        journal.respond("r1", "bought", T0)
    journal.close()
    again = Journal(path)  # kept across a restart
    found = again.get("r1")
    assert found is not None and found.response == "dismissed"
    assert again.respond("r1", None, T0)  # taken back
    found = again.get("r1")
    assert found is not None and found.response is None
    assert again.get("nope") is None
    again.close()


def test_checks_are_scored_once_and_kept(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    for rid, ticker in (("r1", "KXA"), ("r2", "KXB"), ("r3", "KXC")):
        journal.add(item(rid, made(ticker)))
    at = T0 + 1800 * S
    journal.mark("r1", 1800, at, 500_000, 510_000, False)  # sold at 50c
    journal.mark("r2", 1800, at, None, None, True)  # closed: waits for its settlement
    journal.mark("r3", 1800, at, None, None, False)  # an empty book while trading: no score
    before = journal.changes
    assert journal.score(lambda ticker: FEES, at) == 2
    assert journal.changes == before + 1  # a shown score changed (merge review 6)
    assert journal.score(lambda ticker: FEES, at) == 0  # nothing new
    journal.settle("KXB", "yes", at)
    assert journal.score(lambda ticker: FEES, at) == 1
    kept = {e.id: e.marks[0] for e in journal.entries(["r1", "r2", "r3"])}
    assert (kept["r1"].score_e6, kept["r1"].at_settlement) == (40_000, False)
    assert (kept["r2"].score_e6, kept["r2"].at_settlement) == (5_220_000, True)  # $10 - $4.78
    assert kept["r3"].scored and kept["r3"].score_e6 is None
    journal.add(item("r4", made("KXD")))
    journal.mark("r4", 1800, at, 500_000, 510_000, False)
    assert journal.score(lambda ticker: None, at) == 0  # fees not known yet: it waits (review)
    for n in range(5):  # waiting checks made first never hold back a later one (merge review 3)
        journal.add(item(f"w{n}", made(f"KXW{n}")))
        journal.mark(f"w{n}", 1800, at - 10 + n, 500_000, 510_000, False)
    known = {"KXD"}
    assert journal.score(lambda ticker: FEES if ticker in known else None, at, limit=2) == 1
    assert (
        journal.score(lambda ticker: FEES, at) == 5
    )  # the waiting ones, once their fees are known
    journal.add(item("r5", made("KXE")))
    journal.mark("r5", 1800, at, 500_000, 510_000, False)
    hour = at + 3_600 * S
    assert journal.score(lambda ticker: None, hour) == 1  # an hour on: kept unscored
    entry = journal.get("r5")
    assert entry is not None and entry.marks[0].scored and entry.marks[0].score_e6 is None
    assert [e.id for e in journal.entries(["r3", "nope", "r1"])] == ["r3", "r1"]
    assert journal.entries([]) == []
    entry = journal.get("r1")
    assert entry is not None  # the market has since left the index: the kept score stays
    assert recommendation_view(entry).marks[0].score_e6 == 40_000
    journal.close()


def test_a_journal_from_v0_1_8_gains_responses_and_kept_scores(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    old = open_store(path, JOURNAL_MIGRATIONS[:2])  # as v0.1.8 left it
    payload = msgspec.json.encode(made()).decode()
    old.execute(
        "INSERT INTO recommendation VALUES ('r1', 'momentum', '9f2ce1a0', 'KXA', 'yes', ?, ?,"
        " 'expired', NULL, 'momentum', ?)",
        (T0, T0 + 1800 * S, payload),
    )
    old.execute("INSERT INTO mark VALUES ('r1', 1800, ?, 500000, 510000, 0)", (T0 + 1800 * S,))
    old.close()
    journal = Journal(path)  # migration 3, the file backed up first
    assert len(list(tmp_path.glob("journal*.sqlite*"))) >= 2
    assert journal.score(lambda ticker: FEES, T0 + 1800 * S) == 1
    entry = journal.get("r1")
    assert entry is not None and entry.marks[0].score_e6 == 40_000 and entry.response is None
    journal.close()


def test_the_scorecard_by_hand(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    journal = Journal(path)
    at = T0 + 1800 * S
    # bought at 46c; sold at 50c: +$0.04; at 46c: -$0.36 (two fees); at 60c: +$1.05
    for rid, bid in (("r1", 500_000), ("r2", 460_000), ("r3", 600_000), ("r4", None)):
        journal.add(item(rid, made(f"KX{rid}")))
        journal.mark(rid, 1800, at, bid, None if bid is None else bid + 10_000, False)
    journal.add(item("r5", made("KXr5")))
    journal.mark("r5", 1800, at, None, None, True)  # waits for its settlement
    journal.score(lambda ticker: FEES, at)
    (line,) = scorecard(path, T0)
    assert (line.strategy, line.version, line.horizon_s) == ("momentum", "9f2ce1a0", 1800)
    assert (line.checks, line.scored, line.above_zero) == (5, 3, 2)
    assert (line.mean_e6, line.median_e6) == (243_333, 40_000)  # $7.30 / 3 down; the middle
    journal.add(item("r6", made("KXr6")))
    journal.mark("r6", 1800, at, 460_000, 470_000, False)
    journal.score(lambda ticker: FEES, at)
    (line,) = scorecard(path, T0)  # four: the mean of the middle two
    assert (line.scored, line.mean_e6, line.median_e6) == (4, 92_500, -160_000)
    assert scorecard(path, T0 + 1) == []  # all made before the period
    journal.close()


# --- the service ---------------------------------------------------------------------------------


class FakeNotices:
    def __init__(self) -> None:
        self.offered: list[str] = []

    def offer(self, rid: str, rec: Recommendation) -> bool:
        self.offered.append(rec.ticker)
        return True


async def test_the_service_ranks_offers_the_first_and_answers(tmp_path: Path) -> None:
    now = [T0 + S]
    fake = FakeNotices()
    service = JournalService(Journal(tmp_path / "journal.sqlite"), {}, lambda: now[0],
                             fees=lambda ticker: FEES, notices=fake)  # type: ignore[arg-type]  # fmt: skip
    service._write([item("r1", made("KXA", 40_000)), item("r2", made("KXB", 10_000)),
                    item("r3", made("KXC", 90_000)), item("r1", made("KXA", 40_000))])  # fmt: skip
    assert fake.offered == ["KXA", "KXC"]  # each first when it came; r1 again is no news
    assert service.board.top() == ["r3", "r1", "r2"]
    assert service.respond("r3", "dismissed") and service.board.top() == ["r1", "r2"]
    assert service.respond("nope", "taken") is False
    fresh = JournalService(service.journal, {}, lambda: now[0], fees=lambda ticker: FEES)
    changes = fresh.journal.changes
    fresh.load()  # a restart: the dismissed one still left out
    assert fresh.board.top() == ["r1", "r2"]
    assert fresh.journal.changes == changes + 1  # a page built before the load is redone (review 2)
    await service.refresh_card()  # on a worker thread
    assert service.card == [] and service.card_at_ns == now[0]
    now[0] = T0 + 1800 * S
    service._write([])
    assert service.board.top() == [] and service.journal.active_entries() == []
    service.journal.close()


# --- the notifications ---------------------------------------------------------------------------


class FakeNotifier:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str, bool]] = []

    def show(self, title: str, text: str, urgent: bool) -> None:
        self.shown.append((title, text, urgent))


def test_notices_three_an_hour_and_none_at_night(tmp_path: Path) -> None:
    toasts = FakeNotifier()
    desk = AlertDesk(toasts, tmp_path, QuietHours.parse("22:00", "07:00"), state_name=ENGINE_DESK)
    now = [NOON]
    notices = Notices(desk, lambda ticker: "Made-up market", lambda: now[0])
    # the same market twice: each recommendation its own key, so the desk shows both (review 5)
    offered = [notices.offer(f"r{n}", made("KXA")) for n in range(4)]
    assert offered == [True, True, True, False]
    now[0] = NOON + timedelta(minutes=61)  # the hour is up
    assert notices.offer("r9", made("KX9")) is True
    now[0] = NIGHT  # quiet hours: none (it would be stale by morning)
    assert notices.offer("rN", made("KXN")) is False
    notices.close(wait=True)
    now[0] = NOON + timedelta(hours=3)
    assert notices.offer("late", made("KXL")) is False  # closed (the engine stopping): review 1
    assert len(toasts.shown) == 4
    assert toasts.shown[0] == ("IKARS: Buy YES at 46c", "Made-up market: made up: up 5c in 5 min",
                               False)  # fmt: skip
    assert (tmp_path / ENGINE_DESK).exists() and not (tmp_path / "alert-desk.json").exists()
    assert (cents_text(465_000), cents_text(10_000), cents_text(1)) == ("46.5c", "1c", "0.0001c")


def test_two_desks_share_the_alert_log_line_by_line(tmp_path: Path) -> None:
    desks = [AlertDesk(FakeNotifier(), tmp_path, QuietHours.parse("22:00", "07:00"),
                       state_name=name) for name in ("alert-desk.json", ENGINE_DESK)]  # fmt: skip

    def write(desk: AlertDesk, tag: str) -> None:
        for n in range(100):
            desk.raise_alert(Alert(f"{tag}{n}", HIGH, "t" * 200, "x" * 2_000), NOON)

    threads = [
        threading.Thread(target=write, args=(d, t)) for d, t in zip(desks, "ab", strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = (tmp_path / ALERT_LOG).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200 and all(json.loads(line)["text"] == "x" * 2_000 for line in lines)


def test_the_scorecard_reads_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite"
    Journal(path).close()
    assert scorecard(path, T0) == []
    with pytest.raises(sqlite3.OperationalError):  # read-only: a missing file is not made
        scorecard(tmp_path / "none.sqlite", T0)
