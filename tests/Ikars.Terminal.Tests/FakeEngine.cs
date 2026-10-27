using System.Runtime.CompilerServices;
using System.Text.Json;
using Ikars.Client;
using Ikars.Contracts;

namespace Ikars.Terminal.Tests;

/// <summary>A made-up engine (every value invented) behind <see cref="IIkarsApi"/>, with what each call returns settable by a test.</summary>
internal sealed class FakeEngine : IIkarsApi
{
    public static MarketView Market(string ticker, bool watched = true, string evt = "KXDEMO-26JAN01", long? bid = 410_000) =>
        new(ticker, $"Made up {ticker}", evt, "KXDEMO", "Demo", "active", "2026-01-01T12:00:00Z", 100_000, bid, 430_000, 420_000, watched);

    public List<MarketView> Markets { get; } = [Market("KXDEMO-26JAN01-A"), Market("KXDEMO-26JAN01-B"), Market("KXOTHER-1", evt: "KXOTHER")];

    public PortfolioView Portfolio { get; set; } = new(
        3_000_000,
        1_518_000,
        72_000,
        [new PositionView("KXDEMO-26JAN01-A", "Made up A", "yes", 300, 1_518_000, "KXDEMO-26JAN01", "KXDEMO", "Demo", 550_000, 60_000, 72_000)],
        new Dictionary<string, long> { ["Demo"] = 1_518_000 },
        new Dictionary<string, long> { ["KXDEMO-26JAN01"] = 1_518_000 });

    public List<AlertView> Alerts { get; } = [.. Enumerable.Range(1, 10).Select(n => new AlertView(n, $"k{n}", n % 3 == 0 ? "urgent" : n % 2 == 0 ? "high" : "normal", "t", $"alert {n}", "2026-01-01T00:00:00Z", "shown"))];

    public HealthView Health { get; set; } = new("2026-01-01T00:00:00Z", Parse("""
        {"kalshi": {"feed": {"connected": true}, "open_gaps": 0}, "ledger": {"reconciled_exact": true}, "backup": {"hours_since": 3}}
        """));

    public Func<string, CandlePage> Candles { get; set; } = ticker => new CandlePage(ticker, 1, [new Candle("2026-01-01T12:01:00Z", 410_000, 430_000, 400_000, 420_000, 410_000, 430_000, 2_500)]);

    public int Calls { get; private set; }

    /// <summary>Whether the stream opens (otherwise it waits silently).</summary>
    public bool OpensStream { get; set; }

    /// <summary>Set when a subscription change arrives.</summary>
    public TaskCompletionSource Subscribed { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    /// <summary>What a subscription change fails with, if anything.</summary>
    public Exception? SubscribeFailure { get; set; }

    public static IReadOnlyDictionary<string, JsonElement> Parse(string json) =>
        JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(json) ?? [];

    public Task<Hello> HelloAsync(CancellationToken cancellationToken = default) => Done(new Hello("1.0", "0.0.0", "x"));

    public Task<HealthView> HealthAsync(CancellationToken cancellationToken = default) => Done(Health);

    public Task<MarketPage> MarketsAsync(string? query, string? category, int offset, int limit, CancellationToken cancellationToken = default)
    {
        var found = Markets.Where(m => (query is null || m.Ticker.Contains(query, StringComparison.OrdinalIgnoreCase)) && (category is null || m.Category == category)).ToList();
        return Done(new MarketPage([.. found.Skip(offset).Take(limit)], found.Count, offset));
    }

    public Task<MarketView> MarketAsync(string ticker, CancellationToken cancellationToken = default) =>
        Markets.FirstOrDefault(m => m.Ticker == ticker) is { } market ? Done(market) : throw new ApiException(404, "not_found", "no such market");

    public Task<BookView> BookAsync(string ticker, CancellationToken cancellationToken = default) =>
        Done(new BookView(ticker, true, [.. Enumerable.Range(0, 12).Select(n => new Level(410_000 - (n * 10_000), 1_250))], [new Level(560_000, 300)]));

    public Task<CandlePage> CandlesAsync(string ticker, int intervalMinutes, int hours, CancellationToken cancellationToken = default) => Done(Candles(ticker));

    public Task<PortfolioView> PortfolioAsync(CancellationToken cancellationToken = default) => Done(Portfolio);

    public Task<LedgerStatusView> LedgerStatusAsync(CancellationToken cancellationToken = default) =>
        Done(new LedgerStatusView(new Dictionary<string, long> { ["fill"] = 2 }, 150, true, true, "America/Denver"));

    public Task<LedgerPage> LedgerRecordsAsync(long? before, int limit, string? ticker = null, CancellationToken cancellationToken = default)
    {
        var records = Enumerable.Range(1, 150).Reverse()
            .Where(seq => before is null || seq < before)
            .Select(seq => new LedgerRecordView(seq, "fill", "2026-01-01T00:00:00Z", "2025-12-31T17:00:00-07:00", "fill", seq % 2 == 0 ? "KXDEMO-26JAN01-A" : null, -400_000, new Dictionary<string, JsonElement>(), new Dictionary<string, JsonElement>(), null))
            .Where(r => ticker is null || r.Ticker == ticker)
            .Take(limit)
            .ToList();
        var last = records.Count == limit ? records[^1].Seq : (long?)null;
        return Done(new LedgerPage(records, 150, last));
    }

    public Task<AlertPage> AlertsAsync(long? after, int limit, CancellationToken cancellationToken = default) =>
        Done(new AlertPage([.. Alerts.Where(a => after is null || a.Line > after).TakeLast(limit)]));

    public Task<RecommendationPage> RecommendationsAsync(CancellationToken cancellationToken = default) => Done(new RecommendationPage([]));

    public Task<GapPage> GapsAsync(int days, CancellationToken cancellationToken = default) =>
        Done(new GapPage([new GapView(1, "trade", "reconnect (connection_lost)", "2026-01-01T00:00:00Z", "2026-01-01T00:00:03Z", "backfill", "done", "71 trades fetched")]));

    public Task SetSubscriptionsAsync(string streamId, Subscriptions subscriptions, CancellationToken cancellationToken = default)
    {
        Subscribed.TrySetResult();
        return SubscribeFailure is { } failure ? Task.FromException(failure) : Task.CompletedTask;
    }

    public async IAsyncEnumerable<StreamEvent> StreamAsync(long? afterAlert, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        if (OpensStream)
        {
            yield return new StreamEvent.Opened(new StreamOpened("s1", 100));
        }

        await Task.Delay(Timeout.Infinite, cancellationToken).ConfigureAwait(false);
    }

    private Task<T> Done<T>(T value)
    {
        Calls++;
        return Task.FromResult(value);
    }
}
