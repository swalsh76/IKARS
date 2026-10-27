"""Opt-in checks of the account reads (PLAN D18) against the real Kalshi service.

    $env:IKARS_LIVE = '1'      # the key: IKARS_KALSHI_KEY_ID and IKARS_KALSHI_PRIVATE_KEY_PATH
    pytest packages/ikars-core/tests/test_live_account.py -q

GET only, about a dozen signed requests. They ask what a fake cannot: does Kalshi accept the account
reader's signature on each route, do the owner's real records parse into the models, and in what
unit are the timestamps Kalshi does not document.

**No account value is ever written anywhere**: the summary (file named by ``IKARS_LIVE_SUMMARY``)
holds only status codes, record counts, field names and timestamp units, plus the API usage tier
and its read and write rates (rate limits, not money).
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from ikars_core.account import Account
from ikars_core.auth import AccountSigner, AuthError
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import TypedPage
from ikars_core.rest import KalshiAccountClient

pytestmark = pytest.mark.integration

LINES: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def summary() -> Iterator[None]:
    yield
    target = os.environ.get("IKARS_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


@pytest.fixture
def account() -> Account:
    try:
        signer = AccountSigner.from_environment()
    except AuthError:
        pytest.skip("no usable key in IKARS_KALSHI_KEY_ID and IKARS_KALSHI_PRIVATE_KEY_PATH")
    config = KalshiConfig(environment=Environment.PRODUCTION, rate_limit_per_second=2.0)
    return Account(KalshiAccountClient(config, signer))


def unit(value: int) -> str:
    """Seconds or milliseconds, judged by size (any time from 2001 to 2286)."""
    if 10**9 <= value < 10**10:
        return "seconds"
    if 10**12 <= value < 10**13:
        return "milliseconds"
    return "unknown"


async def test_limits_and_balance(account: Account) -> None:
    limits = await account.limits()
    LINES.append(
        f"account limits: tier {limits.usage_tier}; read {limits.read.refill_rate}/s burst "
        f"{limits.read.bucket_capacity}; write {limits.write.refill_rate}/s burst "
        f"{limits.write.bucket_capacity}; grants {len(limits.grants)}"
    )
    balance = await account.balance()  # parsed (the two forms agree), never reported
    LINES.append(f"balance: read and parsed; updated_ts is in {unit(balance.updated_ts)}")


async def test_positions_live_and_archived(account: Account) -> None:
    for status in ("unsettled", "settled"):
        p = await account.positions(settlement_status=status)
        markets, events = len(p.market_positions), len(p.event_positions)
        LINES.append(f"positions {status}: {markets} markets, {events} events")
    archived = await account.positions(historical=True)
    LINES.append(f"positions archived: {len(archived.market_positions)} markets")


async def first_page(name: str, pages: AsyncIterator[TypedPage[Any]]) -> None:
    page = await anext(pages)
    more = "more pages" if page.next_cursor else "no more pages"
    LINES.append(f"{name}: first page {len(page.items)} records, {more}")


async def test_fills_settlements_and_history(account: Account) -> None:
    await first_page("fills", account.fills(limit=100))
    await first_page("historical fills", account.fills(historical=True, limit=100))
    await first_page("settlements", account.settlements(limit=100))


async def first_page_units(name: str, pages: AsyncIterator[TypedPage[Any]]) -> None:
    page = await anext(pages)
    units = sorted({unit(r.created_ts) for r in page.items})
    LINES.append(f"{name}: first page {len(page.items)} records; created_ts in {units or '-'}")


async def test_deposits_withdrawals_and_transfers(account: Account) -> None:
    await first_page_units("deposits", account.deposits())
    await first_page_units("withdrawals", account.withdrawals())
    await first_page_units("instance transfers", account.instance_transfers())
