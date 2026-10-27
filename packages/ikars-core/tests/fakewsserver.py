"""A local WebSocket server that plays a script, standing in for Kalshi in the feed tests.

Each connection runs the next handler in the list (the last one repeats), so a test can say what
the first, second and third connection do: send frames, go quiet, drop, refuse a command.
"""

import contextlib
import json
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Self

import websockets.asyncio.server
from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

Handler = Callable[[ServerConnection], Awaitable[None]]
PATH = "/trade-api/ws/v2"


class FakeKalshi:
    def __init__(self, *handlers: Handler) -> None:
        self._handlers = handlers
        self.connections = 0
        self.handshakes: list[dict[str, str]] = []
        self.commands: list[list[dict[str, Any]]] = []  # per connection, what the client sent
        self._server: Any = None
        self.url = ""

    async def __aenter__(self) -> Self:
        self._server = await websockets.asyncio.server.serve(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}{PATH}"
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, ws: ServerConnection) -> None:
        index = self.connections
        self.connections += 1
        request = ws.request
        self.handshakes.append(
            {k.lower(): v for k, v in (request.headers.items() if request else [])}
        )
        self.commands.append([])
        handler = self._handlers[min(index, len(self._handlers) - 1)]
        ws.commands = self.commands[index]  # type: ignore[attr-defined]
        with contextlib.suppress(ConnectionClosed):
            await handler(ws)


async def read_command(ws: ServerConnection) -> dict[str, Any]:
    command: dict[str, Any] = json.loads(await ws.recv())
    ws.commands.append(command)  # type: ignore[attr-defined]
    return command


def subscribed(command: dict[str, Any], sid: int, seq: int | None = None) -> str:
    body: dict[str, Any] = {
        "id": command["id"],
        "type": "subscribed",
        "msg": {"channel": command["params"]["channels"][0], "sid": sid},
    }
    if seq is not None:
        body["seq"] = seq
    return json.dumps(body)


def error_reply(command: dict[str, Any], code: int, message: str) -> str:
    body = {"id": command["id"], "type": "error", "msg": {"code": code, "msg": message}}
    return json.dumps(body)


def trade(number: int, sid: int | None = 1, seq: int | None = None) -> str:
    body: dict[str, Any] = {
        "type": "trade",
        "msg": {
            "trade_id": f"t-{number}",
            "market_ticker": "M",
            "yes_price_dollars": "0.5600",
            "no_price_dollars": "0.4400",
            "count_fp": "1.00",
            "taker_side": "yes",
        },
    }
    if sid is not None:
        body["sid"] = sid
    if seq is not None:
        body["seq"] = seq
    return json.dumps(body)


def subscribe_then(*frames: str, sid: int = 1, then: Handler | None = None) -> Handler:
    """A handler: answer one subscribe, send the frames, then run ``then`` (default: wait)."""

    async def handler(ws: ServerConnection) -> None:
        command = await read_command(ws)
        await ws.send(subscribed(command, sid))
        for frame in frames:
            await ws.send(frame)
        if then is not None:
            await then(ws)
        else:
            await ws.wait_closed()

    return handler
