using System.Globalization;

namespace Ikars.Terminal.Live;

/// <summary>
/// Fixed-point values as text (PLAN: money is integers everywhere): dollars in millionths and counts in hundredths are divided exactly as decimals, never through
/// floating point.
/// </summary>
internal static class Formatting
{
    private static readonly CultureInfo Us = CultureInfo.GetCultureInfo("en-US");

    /// <summary>Dollars: <c>$1,234.56</c>, or a dash when unknown. A price below a dollar keeps its cents: <c>$0.42</c>.</summary>
    public static string Dollars(long? e6) => e6 is { } value ? (value / 1_000_000m).ToString("$#,##0.00;-$#,##0.00", Us) : "—";

    /// <summary>A signed amount: <c>+$0.07</c> or <c>-$0.80</c>.</summary>
    public static string Signed(long? e6) => e6 is { } value ? (value >= 0 ? "+" : string.Empty) + Dollars(value) : "—";

    /// <summary>A price as cents, as Kalshi shows it: <c>42¢</c>, <c>42.5¢</c>.</summary>
    public static string Cents(long? e6) => e6 is { } value ? (value / 10_000m).ToString("0.##", Us) + "¢" : "—";

    /// <summary>Contracts: <c>12.5</c>, <c>1,000</c>.</summary>
    public static string Contracts(long? e2) => e2 is { } value ? (value / 100m).ToString("#,##0.##", Us) : "—";

    /// <summary>An ISO time as the workstation's local time, short.</summary>
    public static string LocalTime(string iso) =>
        DateTimeOffset.TryParse(iso, Us, DateTimeStyles.AssumeUniversal, out var moment) ? moment.ToLocalTime().ToString("yyyy-MM-dd HH:mm", Us) : iso;
}
