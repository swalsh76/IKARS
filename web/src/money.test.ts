import { describe, expect, it } from "vitest";
import { MoneyError, cents, contracts, dollars, e2, e6, percent, share } from "./money";

describe("dollars", () => {
  it("formats millionths exactly, rounding half away from zero", () => {
    expect(dollars(e6(1_234_560_000))).toBe("$1,234.56");
    expect(dollars(e6(0))).toBe("$0.00");
    expect(dollars(e6(2_037))).toBe("$0.00");
    expect(dollars(e6(5_000))).toBe("$0.01"); // exactly half a cent rounds up
    expect(dollars(e6(-5_000))).toBe("-$0.01"); // and away from zero below it
    expect(dollars(e6(-420_000))).toBe("-$0.42");
    expect(dollars(e6(2_037), { places: 6 })).toBe("$0.002037");
    expect(dollars(e6(1_000_000), { places: 0 })).toBe("$1");
  });

  it("signs gains when asked, never a negative zero", () => {
    expect(dollars(e6(12_400_000), { signed: true })).toBe("+$12.40");
    expect(dollars(e6(-12_400_000), { signed: true })).toBe("-$12.40");
    expect(dollars(e6(0), { signed: true })).toBe("$0.00");
    expect(dollars(e6(-1), { signed: true })).toBe("$0.00");
  });

  it("is exact at the largest safe amount (about $9 billion)", () => {
    expect(dollars(e6(Number.MAX_SAFE_INTEGER), { places: 6 })).toBe("$9,007,199,254.740991");
  });

  it("refuses places it cannot show", () => {
    expect(() => dollars(e6(1), { places: 7 })).toThrow(MoneyError);
    expect(() => dollars(e6(1), { places: -1 })).toThrow(MoneyError);
  });
});

describe("the integer check", () => {
  it("refuses fractions and amounts past 2^53", () => {
    expect(() => e6(0.5)).toThrow(MoneyError);
    expect(() => e6(2 ** 53)).toThrow(MoneyError);
    expect(() => e2(Number.NaN)).toThrow(MoneyError);
    expect(() => dollars(1.5 as never)).toThrow(MoneyError);
  });
});

describe("cents and contracts", () => {
  it("shows prices in cents with a sub-cent tick when there is one", () => {
    expect(cents(e6(420_000))).toBe("42¢");
    expect(cents(e6(425_000))).toBe("42.5¢");
    expect(cents(e6(-60_000))).toBe("-6¢");
  });

  it("shows counts without trailing zeros", () => {
    expect(contracts(e2(1_250))).toBe("12.5");
    expect(contracts(e2(300))).toBe("3");
    expect(contracts(e2(100_000))).toBe("1,000");
    expect(contracts(e2(-5))).toBe("-0.05");
  });
});

describe("share", () => {
  it("is a drawing width only, clamped to 0..100", () => {
    expect(share(e6(1_518_000), e6(3_036_000))).toBe(50);
    expect(share(e6(1), e6(3))).toBe(33.33);
    expect(share(e6(5), e6(0))).toBe(0);
    expect(share(e6(-5), e6(10))).toBe(0);
    expect(share(e6(20), e6(10))).toBe(100);
  });
});

describe("percent", () => {
  it("shows a ratio in millionths as a percent, exactly", () => {
    expect(percent(8_368)).toBe("0.8%");
    expect(percent(401_664, { signed: true })).toBe("+40.2%");
    expect(percent(-75_314, { signed: true })).toBe("-7.5%");
    expect(percent(40_166_400)).toBe("4,016.6%");
    expect(percent(4, { signed: true })).toBe("0.0%"); // rounds to nothing: no sign
    expect(percent(-4)).toBe("0.0%");
    expect(percent(123_456, { places: 2 })).toBe("12.35%");
    expect(() => percent(0.5)).toThrow(MoneyError);
  });
});
