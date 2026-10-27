using System.Text.Json;
using System.Text.Json.Serialization;

namespace Ikars.Contracts;

// The engine's loopback API payloads (PLAN D3, M1.7), written by hand to match
// packages/ikars-engine/src/ikars_engine/contracts.py. They are checked by Ikars.Contracts.Tests against the golden
// examples the engine exports (contracts/examples): a field added, renamed or retyped on either side fails that test.
// Money is in dollar millionths (E6) and counts in contract hundredths (E2), as long integers; never floating point.

/// <summary>JSON options for every contract: snake_case names, unknown fields refused, nullability respected.</summary>
public static class ContractJson
{
    /// <summary>The options the terminal reads and writes the API with.</summary>
    public static JsonSerializerOptions Options { get; } = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        RespectNullableAnnotations = true,
        RespectRequiredConstructorParameters = true,
    };

    /// <summary>The API version the terminal is built for; the engine's major number must match.</summary>
    public const string ApiVersion = "1.0";
}

/// <summary><c>GET /v1/hello</c>: the version handshake.</summary>
public sealed record Hello(string ApiVersion, string EngineVersion, string SchemaSha256);

/// <summary><c>GET /v1/health</c>.</summary>
public sealed record HealthView(string AtUtc, IReadOnlyDictionary<string, JsonElement> Parts);

/// <summary>A market from the engine's index, with its live quote.</summary>
public sealed record MarketView(
    string Ticker,
    string Title,
    string EventTicker,
    string SeriesTicker,
    string Category,
    string Status,
    string? CloseTimeUtc,
    [property: JsonPropertyName("volume_24h_e2")] long Volume24hE2,
    long? YesBidE6,
    long? YesAskE6,
    long? LastPriceE6,
    bool Watched);

/// <summary><c>GET /v1/markets</c>.</summary>
public sealed record MarketPage(IReadOnlyList<MarketView> Items, long Total, long Offset);

/// <summary>One price level of a book.</summary>
public sealed record Level(long PriceE6, long CountE2);

/// <summary>A market's book: both sides' bids, best first.</summary>
public sealed record BookView(string Ticker, bool Ready, IReadOnlyList<Level> Yes, IReadOnlyList<Level> No);

/// <summary>An open position, marked at the bid after the exit fee.</summary>
public sealed record PositionView(
    string Ticker,
    string Title,
    string Side,
    long CountE2,
    long CostE6,
    string EventTicker,
    string SeriesTicker,
    string Category,
    long? ExitPriceE6,
    long? ExitFeeE6,
    long? UnrealisedE6);

/// <summary><c>GET /v1/portfolio</c>.</summary>
public sealed record PortfolioView(
    long CashE6,
    long CostE6,
    long? UnrealisedE6,
    IReadOnlyList<PositionView> Positions,
    IReadOnlyDictionary<string, long> ExposureByCategory,
    IReadOnlyDictionary<string, long> ExposureByEvent);

/// <summary><c>GET /v1/ledger/status</c>.</summary>
public sealed record LedgerStatusView(
    IReadOnlyDictionary<string, long> Records,
    long HeadSeq,
    bool? Verified,
    bool? ReconciledExact,
    string TaxZone);

/// <summary>One ledger record.</summary>
public sealed record LedgerRecordView(
    long Seq,
    string Kind,
    string TimeUtc,
    string TimeLocal,
    string Source,
    string? Ticker,
    long? CashE6,
    IReadOnlyDictionary<string, JsonElement> Fields,
    IReadOnlyDictionary<string, JsonElement> Context,
    string? Note);

/// <summary><c>GET /v1/ledger/records</c>, newest first.</summary>
public sealed record LedgerPage(IReadOnlyList<LedgerRecordView> Items, long HeadSeq, long? Before);

/// <summary>One line of the alert log.</summary>
public sealed record AlertView(long Line, string Key, string Priority, string Title, string Text, string At, string Outcome);

/// <summary><c>GET /v1/alerts</c>.</summary>
public sealed record AlertPage(IReadOnlyList<AlertView> Items);

/// <summary>One period of a market's prices: the traded band (null when nothing traded), the closing bid and ask, volume.</summary>
public sealed record Candle(
    string EndUtc,
    long? OpenE6,
    long? HighE6,
    long? LowE6,
    long? CloseE6,
    long? BidCloseE6,
    long? AskCloseE6,
    long? VolumeE2);

/// <summary><c>GET /v1/markets/{ticker}/candles</c>, oldest first.</summary>
public sealed record CandlePage(string Ticker, long IntervalMinutes, IReadOnlyList<Candle> Items);

/// <summary>A recommendation (filled from M1.12).</summary>
public sealed record RecommendationView(string Id, string Ticker, string Side, string Summary, string AtUtc);

/// <summary><c>GET /v1/recommendations</c>.</summary>
public sealed record RecommendationPage(IReadOnlyList<RecommendationView> Items);

/// <summary><c>PUT /v1/stream/{id}/subscriptions</c>.</summary>
public sealed record Subscriptions(IReadOnlyList<string> Quotes, IReadOnlyList<string> Books);

/// <summary>The stream's first event.</summary>
public sealed record StreamOpened(string StreamId, long CoalesceMs);

/// <summary>A stream event: a market's quote changed.</summary>
public sealed record QuoteUpdate(string Ticker, long? YesBidE6, long? YesAskE6, long? LastPriceE6, long? ReceivedNs);

/// <summary>Any refusal from the API.</summary>
public sealed record ApiError(string Error, string Detail);
