"""The exchange's status, recorded when it changes (from KAPS ``exchange.py``).

Kalshi pauses trading for maintenance; a pause is not an outage, and an outage must not hide inside
one, so the status is polled every minute and a row is stored only when it differs from the last
one (and the first time): the table is a history of changes, not a log of polls. The schedule is
fetched rarely and kept in memory for the tradability checks (PLAN D20 E).
"""

from ikars_core.endpoints import MarketData
from ikars_core.models import ExchangeSchedule, ExchangeStatus

from ikars_engine.clocks import EventClock
from ikars_engine.store import StateStore


class ExchangeWatch:
    def __init__(self, data: MarketData, state: StateStore, clock: EventClock) -> None:
        self._data = data
        self._state = state
        self._clock = clock
        self.status: ExchangeStatus | None = None
        self.schedule: ExchangeSchedule | None = None
        self.changes = 0

    @property
    def trading(self) -> bool | None:
        """Whether trading is active, or None before the first poll."""
        return None if self.status is None else self.status.trading_active

    async def poll(self) -> bool:
        """Read the status; store it if it changed. Returns whether it changed."""
        status = await self._data.exchange_status()
        self.status = status
        detail = status.model_dump_json(include={"shards"})
        current = (status.exchange_active, status.trading_active, detail)
        if self._state.last_exchange_status() == current:
            return False
        self._state.add_exchange_status(self._clock.now_ns(), *current)
        self.changes += 1
        return True

    async def refresh_schedule(self) -> None:
        self.schedule = await self._data.exchange_schedule()
