// Money and counts as the engine sends them: integers, dollars in millionths (_e6) and contracts in
// hundredths (_e2), exact in JavaScript up to 2^53 (contracts.py). The terminal does **no money
// arithmetic** (PLAN D1 as amended): it only formats, and formats exactly, by splitting the integer
// with BigInt, never through a float.

declare const e6Brand: unique symbol;
declare const e2Brand: unique symbol;
/** Dollars in millionths, as the engine sends them. */
export type E6 = number & { readonly [e6Brand]: true };
/** Contracts in hundredths, as the engine sends them. */
export type E2 = number & { readonly [e2Brand]: true };

export class MoneyError extends Error {}

function whole(value: number): bigint {
  if (!Number.isSafeInteger(value)) {
    throw new MoneyError(`not an exact integer amount: ${String(value)}`);
  }
  return BigInt(value);
}

/** Takes the engine's integer as money (checked: an inexact or fractional value is refused). */
export function e6(value: number): E6 {
  whole(value);
  return value as E6;
}

/** Takes the engine's integer as a count (checked like ``e6``). */
export function e2(value: number): E2 {
  whole(value);
  return value as E2;
}

function grouped(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function scaled(value: bigint, scale: bigint, places: number): [boolean, string, string] {
  const negative = value < 0n;
  const size = negative ? -value : value;
  const unit = scale / 10n ** BigInt(places);
  const rounded = (size + unit / 2n) / unit; // half away from zero, in whole units of the last place
  const shown = 10n ** BigInt(places);
  const integer = (rounded / shown).toString();
  const fraction = places ? (rounded % shown).toString().padStart(places, "0") : "";
  return [negative && rounded !== 0n, integer, fraction];
}

export interface DollarFormat {
  /** Decimal places shown (2: cents; prices may need 4). Rounded half away from zero. */
  places?: number;
  /** Show + for gains. */
  signed?: boolean;
}

/** ``$1,234.56``, ``-$0.42``, ``+$12.40`` (signed). */
export function dollars(value: E6, { places = 2, signed = false }: DollarFormat = {}): string {
  if (places < 0 || places > 6) throw new MoneyError("between 0 and 6 decimal places");
  const [negative, integer, fraction] = scaled(whole(value), 1_000_000n, places);
  const sign = negative ? "-" : signed && value > 0 ? "+" : "";
  return `${sign}$${grouped(integer)}${fraction ? `.${fraction}` : ""}`;
}

/** A price in cents, the way Kalshi shows it: ``42¢``, or ``42.5¢`` for a sub-cent tick. */
export function cents(value: E6): string {
  const [negative, integer, fraction] = scaled(whole(value), 10_000n, 1);
  return `${negative ? "-" : ""}${integer}${fraction === "0" ? "" : `.${fraction}`}¢`;
}

/** A count of contracts: ``12``, ``12.5``, ``1,000``. */
export function contracts(value: E2): string {
  const [negative, integer, fraction] = scaled(whole(value), 100n, 2);
  const trimmed = fraction.replace(/0+$/, "");
  return `${negative ? "-" : ""}${grouped(integer)}${trimmed ? `.${trimmed}` : ""}`;
}

/** A share of a total, for drawing only (a bar's width): never money arithmetic shown as money. */
export function share(part: E6, total: E6): number {
  const p = whole(part);
  const t = whole(total);
  if (t <= 0n || p <= 0n) return 0;
  const percent = Number((p * 10_000n) / t) / 100;
  return Math.min(100, percent);
}

/** A price in cents as a plain number, for a chart's axis only (drawing, never money arithmetic;
 * at most 0.0001-cent off, which no chart shows). */
export function drawingCents(value: E6): number {
  return Number(whole(value)) / 10_000;
}
