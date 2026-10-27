"""What flows through the engine: references, items and the links between them.

PLAN D1, D19 and D20 A.

Every thing IKARS knows about (a market, a source item, a signal, a recommendation, a position) has
one typed reference, ``Ref(kind, id)``. An ``Item`` is something that arrived or was produced, and
always carries **two times**: when its publisher says it happened (``published_ns``, which may be
missing or wrong) and when IKARS received it (``received_ns``). Back-tests and replays use only the
received time, so they can never see an item before IKARS could have (D20 A). A ``Link`` joins two
references; the cross-reference panel is built from links, so a new plugin adds links rather than
new screens.

These are ``msgspec`` structs: frozen, cheap to create on the hot path, and the same types the
loopback API (M1.7) sends to the terminal.
"""

from typing import Any

import msgspec


class Ref(msgspec.Struct, frozen=True, array_like=True):
    kind: str  # market, event, series, source_item, signal, recommendation, position, ...
    id: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


class Item(msgspec.Struct, frozen=True, kw_only=True):
    ref: Ref
    source: str  # the plugin (or feed) that produced it
    received_ns: int  # when IKARS received it (wall clock, nanoseconds since the epoch)
    published_ns: int | None = None  # when its publisher says it happened, if it says
    payload: dict[str, Any] = {}
    hops: int = 0  # 0 from a feed; a plugin's emit is one more than the item it was handling


class Link(msgspec.Struct, frozen=True, kw_only=True):
    source: Ref
    target: Ref
    kind: str  # mentions, moved, related, hedges, produced, ...
    weight_e6: int = 1_000_000  # strength in millionths (1.0 = certain)
    made_ns: int  # when IKARS made the link (received time, like an item's)


_encoder = msgspec.json.Encoder()
_item_decoder = msgspec.json.Decoder(Item)


def encode(value: Item | Link | Ref) -> bytes:
    return _encoder.encode(value)


def decode_item(data: bytes) -> Item:
    return _item_decoder.decode(data)
