"""The single numeric representation: integer fixed-point, converted only at the edges.

Two scales, carried in names: dollar amounts in millionths (``*_e6``) and contract counts in
hundredths (``*_e2``). Conversion is exact: a value with more precision than the scale raises
:class:`PrecisionError` instead of being rounded, and a value that would not fit a signed 64-bit
integer (the database ``bigint``) is refused.

Accepted inputs are ``str`` (plain ASCII decimal text, no exponent), ``int`` and
:class:`~decimal.Decimal`. Floats and bools are refused with :class:`TypeError`: a float has
already lost the exact value, so the caller must parse the source text instead (for JSON, with
``json.loads(..., parse_float=str)``).

Compared with the KalshiTerm2 reference this is stricter in three ways, each pinned by the
committed vectors: floats are refused; text must match a plain ASCII decimal grammar; and the
arithmetic is done on integers, so a value with more than 28 significant digits cannot be
silently rounded by a decimal context into looking exact.
"""

import re
from decimal import Decimal
from typing import NewType, overload

E6 = 10**6  # dollars in millionths; also "one dollar" as an amount
E2 = 10**2  # contracts in hundredths
INT64_MAX = 2**63 - 1

DollarsE6 = NewType("DollarsE6", int)
"""An amount of dollars in millionths."""
CountE2 = NewType("CountE2", int)
"""A number of contracts in hundredths."""

_TEXT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)", re.ASCII)
_MAX_EXPONENT = (
    400  # beyond this a Decimal cannot be exact at either scale; refuse without computing
)


class PrecisionError(ValueError):
    """The value cannot be represented exactly at the scale."""


def _scale(value: str | int | Decimal, factor: int) -> int:
    if isinstance(value, bool | float):
        raise TypeError(f"{type(value).__name__} is not accepted; pass a str, int or Decimal")
    if isinstance(value, int):
        result = value * factor
    elif isinstance(value, str):
        if not _TEXT.fullmatch(value):
            raise PrecisionError(f"not a plain decimal number: {value!r}")
        result = _scale_decimal(Decimal(value), factor, value)
    elif isinstance(value, Decimal):
        result = _scale_decimal(value, factor, value)
    else:
        raise TypeError(f"{type(value).__name__} is not accepted; pass a str, int or Decimal")
    if abs(result) > INT64_MAX:
        raise PrecisionError(f"{value!r} does not fit in a bigint at scale 1/{factor}")
    return result


def _scale_decimal(number: Decimal, factor: int, original: object) -> int:
    if not number.is_finite():
        raise PrecisionError(f"not a finite number: {original!r}")
    if number == 0:
        return 0
    if abs(number.adjusted()) > _MAX_EXPONENT:
        raise PrecisionError(f"{original!r} is outside the supported range")
    numerator, denominator = number.as_integer_ratio()  # exact: no decimal context is involved
    scaled = numerator * factor
    if scaled % denominator:
        raise PrecisionError(f"{original!r} has more precision than 1/{factor}")
    return scaled // denominator


@overload
def to_e6(value: None) -> None: ...
@overload
def to_e6(value: str | int | Decimal) -> DollarsE6: ...
def to_e6(value: str | int | Decimal | None) -> DollarsE6 | None:
    """Dollars to millionths. ``None`` stays ``None``."""
    return None if value is None else DollarsE6(_scale(value, E6))


@overload
def to_e2(value: None) -> None: ...
@overload
def to_e2(value: str | int | Decimal) -> CountE2: ...
def to_e2(value: str | int | Decimal | None) -> CountE2 | None:
    """A contract count to hundredths. ``None`` stays ``None``."""
    return None if value is None else CountE2(_scale(value, E2))


def _unscale(value: int, places: int) -> Decimal:
    digits = tuple(int(d) for d in str(abs(value)))
    return Decimal((1 if value < 0 else 0, digits, -places))


def from_e6(value: int) -> Decimal:
    """Millionths back to a dollar amount, exactly (built from its digits: no context rounds it)."""
    return _unscale(value, 6)


def from_e2(value: int) -> Decimal:
    """Hundredths back to a contract count, exactly."""
    return _unscale(value, 2)
