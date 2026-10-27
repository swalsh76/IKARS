"""The watched set: the markets whose order books IKARS keeps (owner: up to 100 markets, and a
budget of 3,000 book deltas a second in total).

The choice, in priority order:

1. **pinned** markets: open positions, live recommendations and markets open in the terminal (fed in
   by later slices; for now the settings' ``watch`` list). Always kept.
2. open markets whose last price is inside the human-paced band (20 to 80 cents, PLAN D17), ranked
   by their volume over the last day.

Each watched market's delta rate is measured. When the measured total exceeds the budget, the
hottest unpinned markets are dropped and held out for an hour (the 15-minute crypto markets can send
800 deltas a second each: one alone would take a quarter of the budget). A market with no
measurement yet counts as zero until it has one.
"""

from dataclasses import dataclass, field

from ikars_engine.kalshi.discovery import MarketIndex

HOUR_NS = 3_600 * 1_000_000_000


@dataclass(slots=True)
class WatchedSet:
    max_markets: int = 100
    max_deltas_per_second: float = 3_000.0
    price_band_e6: tuple[int, int] = (200_000, 800_000)
    hold_out_ns: int = HOUR_NS
    pinned: set[str] = field(default_factory=set)
    current: set[str] = field(default_factory=set)
    rates: dict[str, float] = field(default_factory=dict)  # deltas a second, as last measured
    held_out: dict[str, int] = field(default_factory=dict)  # ticker -> until (ns)
    _counts: dict[str, int] = field(default_factory=dict)
    _since_ns: int | None = None

    def note_delta(self, ticker: str) -> None:
        self._counts[ticker] = self._counts.get(ticker, 0) + 1

    def measure(self, now_ns: int) -> None:
        """Turn the deltas counted since the last measurement into rates."""
        if self._since_ns is not None and now_ns > self._since_ns:
            seconds = (now_ns - self._since_ns) / 1e9
            self.rates = {t: self._counts.get(t, 0) / seconds for t in self.current}
        self._counts = {}
        self._since_ns = now_ns

    def total_rate(self) -> float:
        """The total as last measured (at a rebalance)."""
        return sum(self.rates.get(t, 0.0) for t in self.current)

    def live_rate(self, now_ns: int) -> float:
        """Deltas a second since the last measurement, for the health view."""
        if self._since_ns is None or now_ns <= self._since_ns:
            return 0.0
        return sum(self._counts.values()) / ((now_ns - self._since_ns) / 1e9)

    def choose(self, index: MarketIndex, last_price_e6: dict[str, int], now_ns: int) -> set[str]:
        """The markets to watch now."""
        self.held_out = {t: until for t, until in self.held_out.items() if until > now_ns}
        low, high = self.price_band_e6
        chosen: list[str] = sorted(self.pinned)[: self.max_markets]
        candidates = sorted(
            (
                m
                for m in index.open_markets()
                if m.ticker not in self.pinned
                and m.ticker not in self.held_out
                and low <= last_price_e6.get(m.ticker, (low + high) // 2) <= high
            ),
            key=lambda m: (-m.volume_24h_e2, m.ticker),
        )
        for market in candidates:
            if len(chosen) >= self.max_markets:
                break
            chosen.append(market.ticker)
        # over the delta budget: drop the hottest unpinned markets, and hold them out for a while
        total = sum(self.rates.get(t, 0.0) for t in chosen)
        for ticker in sorted(
            (t for t in chosen if t not in self.pinned), key=lambda t: -self.rates.get(t, 0.0)
        ):
            if total <= self.max_deltas_per_second:
                break
            total -= self.rates.get(ticker, 0.0)
            chosen.remove(ticker)
            self.held_out[ticker] = now_ns + self.hold_out_ns
        return set(chosen)
