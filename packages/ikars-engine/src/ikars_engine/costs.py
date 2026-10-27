"""The monthly cost ceiling across paid services (PLAN D21-5).

Every paid call (an LLM call, an odds-API request, a paid source) is recorded against the plugin
that made it, in dollar millionths like all money. Across the month's spend:

- below the degrade threshold (80% by default): normal;
- from the threshold: plugins are told to **degrade** (the local model, slower polling);
- at the ceiling: paid calls are **paused** until the month turns.

Months are calendar months in UTC, so a new month starts afresh without anyone resetting it.
"""

from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum

from ikars_engine.clocks import EventClock


class Budget(StrEnum):
    NORMAL = "normal"
    DEGRADE = "degrade"
    PAUSED = "paused"


class CostTracker:
    def __init__(self, clock: EventClock, monthly_e6: int, degrade_at_percent: int = 80) -> None:
        if monthly_e6 < 0 or not 0 < degrade_at_percent <= 100:
            raise ValueError("the budget must be non-negative and the threshold 1 to 100 percent")
        self._clock = clock
        self.monthly_e6 = monthly_e6
        self.degrade_at_percent = degrade_at_percent
        self._month = ""
        self._spent: defaultdict[str, int] = defaultdict(int)

    def _roll(self) -> None:
        month = datetime.fromtimestamp(self._clock.now_ns() // 1_000_000_000, UTC).strftime("%Y-%m")
        if month != self._month:
            self._month = month
            self._spent.clear()

    def spend(self, plugin: str, amount_e6: int) -> Budget:
        if isinstance(amount_e6, bool) or not isinstance(amount_e6, int) or amount_e6 < 0:
            raise ValueError("a cost is a non-negative whole number of dollar millionths")
        self._roll()
        self._spent[plugin] += amount_e6
        return self.state()

    def state(self) -> Budget:
        self._roll()
        spent = self.total_e6()
        if spent >= self.monthly_e6:
            return Budget.PAUSED
        if spent * 100 >= self.monthly_e6 * self.degrade_at_percent:
            return Budget.DEGRADE
        return Budget.NORMAL

    def total_e6(self) -> int:
        self._roll()
        return sum(self._spent.values())

    def by_plugin(self) -> dict[str, int]:
        self._roll()
        return dict(sorted(self._spent.items()))

    @property
    def month(self) -> str:
        self._roll()
        return self._month
