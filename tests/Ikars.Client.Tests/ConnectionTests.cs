using System.Runtime.CompilerServices;
using Ikars.Contracts;

namespace Ikars.Client.Tests;

/// <summary>An engine as the terminal sees it: a script of streams, a handshake version, subscriptions remembered.</summary>
internal sealed class FakeApi(string apiVersion = "1.0") : IIkarsApi
{
    public Queue<Func<IEnumerable<StreamEvent>>> Streams { get; } = new();

    public List<long?> StreamsOpenedAfter { get; } = [];

    public List<(string Id, Subscriptions Wanted)> Subscribed { get; } = [];

    public Task<Hello> HelloAsync(CancellationToken cancellationToken = default) => Task.FromResult(new Hello(apiVersion, "0.0.0", "x"));

    public Task<HealthView> HealthAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<MarketPage> MarketsAsync(string? query, string? category, int offset, int limit, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<MarketView> MarketAsync(string ticker, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<BookView> BookAsync(string ticker, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<CandlePage> CandlesAsync(string ticker, int intervalMinutes, int hours, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<PortfolioView> PortfolioAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<LedgerStatusView> LedgerStatusAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<LedgerPage> LedgerRecordsAsync(long? before, int limit, string? ticker = null, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<AlertPage> AlertsAsync(long? after, int limit, CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<RecommendationPage> RecommendationsAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task SetSubscriptionsAsync(string streamId, Subscriptions subscriptions, CancellationToken cancellationToken = default)
    {
        Subscribed.Add((streamId, subscriptions));
        return Task.CompletedTask;
    }

    public async IAsyncEnumerable<StreamEvent> StreamAsync(long? afterAlert, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        StreamsOpenedAfter.Add(afterAlert);
        await Task.Yield();
        foreach (var item in Streams.Dequeue()())
        {
            yield return item;
        }
    }
}

public class ConnectionTests : IDisposable
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private readonly string _folder = Path.Combine(Path.GetTempPath(), "ikars-client-tests", Guid.NewGuid().ToString("N"));

    public ConnectionTests() => Directory.CreateDirectory(_folder);

    public void Dispose()
    {
        Directory.Delete(_folder, recursive: true);
        GC.SuppressFinalize(this);
    }

    private void WriteApiJson(string version = "1.0") =>
        File.WriteAllText(Path.Combine(_folder, "api.json"), $$"""{"port": 5555, "token": "made-up", "api_version": "{{version}}"}""");

    private static StreamEvent.Opened Opened(string id) => new StreamEvent.Opened(new StreamOpened(id, 100));

    private static StreamEvent.Alert Alert(long line) => new StreamEvent.Alert(new AlertView(line, "k", "high", "t", "x", "a", "shown"));

    [Fact]
    public void ApiJsonIsReadOrAbsent()
    {
        Assert.Null(ApiInfo.Read(_folder));
        WriteApiJson();
        Assert.Equal(new ApiInfo(5555, "made-up", "1.0"), ApiInfo.Read(_folder));
        File.WriteAllText(Path.Combine(_folder, "api.json"), "{not json");
        Assert.Null(ApiInfo.Read(_folder));
        File.WriteAllText(Path.Combine(_folder, "api.json"), "{}");
        Assert.Null(ApiInfo.Read(_folder));
        Assert.EndsWith(Path.Combine("IKARS", "data"), ApiInfo.DefaultDataFolder, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("1.0", true)]
    [InlineData("1.7", true)]
    [InlineData("2.0", false)]
    [InlineData("x", false)]
    public void TheHandshakeComparesMajors(string version, bool compatible) => Assert.Equal(compatible, Handshake.Compatible(version));

    [Fact]
    public void TheRunCommandIsSplitLikeWindowsWritesIt()
    {
        Assert.Equal(["C:\\Program Files\\Py\\pythonw.exe", "-m", "ikars_engine", "serve"], CommandLine.Split("\"C:\\Program Files\\Py\\pythonw.exe\" -m ikars_engine serve"));
        Assert.Equal(["a", "b c", "d"], CommandLine.Split("a  \"b c\" d "));
        Assert.Empty(CommandLine.Split("   "));
    }

    [Fact]
    public async Task TheConnectorUsesARunningEngine()
    {
        WriteApiJson();
        var starter = new FakeStarter(() => { });
        var connector = new EngineConnector(_folder, starter, _ => new FakeApi());
        Assert.IsType<FakeApi>(await connector.ConnectOrStartAsync(_ => { }, CancellationToken.None));
        Assert.Equal(0, starter.Starts);
    }

    [Fact]
    public async Task TheConnectorRefusesAnotherMajorVersion()
    {
        WriteApiJson("2.0");
        var connector = new EngineConnector(_folder, new FakeStarter(() => { }), _ => new FakeApi("2.0"));
        var refused = await Assert.ThrowsAsync<IncompatibleEngineException>(() => connector.ConnectAsync(CancellationToken.None));
        Assert.Equal("2.0", refused.EngineVersion);
    }

    [Fact]
    public async Task TheConnectorStartsAnEngineAndWaitsForIt()
    {
        var polls = 0;
        var starter = new FakeStarter(() => { });
        var progress = new List<string>();
        var connector = new EngineConnector(_folder, starter, _ => new FakeApi(), (_, _) =>
        {
            if (++polls == 3)
            {
                WriteApiJson();   // the engine serves on the third look
            }

            return Task.CompletedTask;
        });
        await connector.ConnectOrStartAsync(progress.Add, CancellationToken.None);
        Assert.Equal(1, starter.Starts);
        Assert.Equal(3, polls);
        Assert.StartsWith("starting the engine", progress[0], StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheConnectorGivesUpCleanly()
    {
        var none = new EngineConnector(_folder, new FakeStarter(null), _ => new FakeApi());
        Assert.Contains("install-autostart", (await Assert.ThrowsAsync<EngineUnavailableException>(() => none.ConnectOrStartAsync(_ => { }, CancellationToken.None))).Message, StringComparison.Ordinal);
        var never = new EngineConnector(_folder, new FakeStarter(() => { }), _ => new FakeApi(), (_, _) => Task.CompletedTask)
        {
            StartTimeout = TimeSpan.FromSeconds(6),
            Poll = TimeSpan.FromSeconds(2),
        };
        Assert.Contains("did not start", (await Assert.ThrowsAsync<EngineUnavailableException>(() => never.ConnectOrStartAsync(_ => { }, CancellationToken.None))).Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheWatcherSubscribesResumesAndBacksOff()
    {
        var api = new FakeApi();
        api.Streams.Enqueue(() => [Opened("s1"), Alert(4), Alert(5)]);              // then it ends
        api.Streams.Enqueue(() => throw new IOException("dropped"));               // a failure
        api.Streams.Enqueue(() => [Opened("s2"), Alert(6)]);
        var waits = new List<TimeSpan>();
        using var stop = new CancellationTokenSource();
        var watcher = new StreamWatcher(_ => Task.FromResult<IIkarsApi>(api), (wait, _) =>
        {
            waits.Add(wait);
            if (api.Streams.Count == 0)
            {
                stop.Cancel();
            }

            return Task.CompletedTask;
        });
        await watcher.SetWantedAsync(new Subscriptions(["A"], ["A"]), Ct);   // before any stream: remembered
        var events = new List<StreamEvent>();
        var notices = new List<string>();
        await watcher.RunAsync(events.Add, notices.Add, stop.Token);
        Assert.Equal([null, 5L, 5L], api.StreamsOpenedAfter);   // resumed after the last alert seen
        Assert.Equal(["s1", "s2"], api.Subscribed.Select(s => s.Id));
        Assert.All(api.Subscribed, s => Assert.Equal(["A"], s.Wanted.Quotes));
        Assert.Equal(6, watcher.AlertLine);
        Assert.Equal(TimeSpan.FromSeconds(1), waits[0]);
        Assert.Contains(notices, n => n.StartsWith("the connection dropped", StringComparison.Ordinal));
        Assert.Equal(5, events.Count);
    }

    [Fact]
    public async Task TheWatcherAppliesNewSubscriptionsToAnOpenStreamAndStopsOnAnIncompatibleEngine()
    {
        var api = new FakeApi();
        var watcher = new StreamWatcher(_ => throw new IncompatibleEngineException("2.0"), (_, _) => Task.CompletedTask);
        var notices = new List<string>();
        await watcher.RunAsync(_ => { }, notices.Add, CancellationToken.None);
        Assert.Contains("API 2.0", notices.Single(), StringComparison.Ordinal);

        api.Streams.Enqueue(() => [Opened("s9")]);
        using var stop = new CancellationTokenSource();
        StreamWatcher? live = null;
        live = new StreamWatcher(_ => Task.FromResult<IIkarsApi>(api), (_, _) =>
        {
            stop.Cancel();
            return Task.CompletedTask;
        });
        await live.RunAsync(e =>
        {
            if (e is StreamEvent.Opened)
            {
                live.SetWantedAsync(new Subscriptions(["B"], []), Ct).GetAwaiter().GetResult();   // while open: sent at once
            }
        }, _ => { }, stop.Token);
        Assert.Equal(["s9", "s9"], api.Subscribed.Select(s => s.Id));
        Assert.Equal(["B"], api.Subscribed[^1].Wanted.Quotes);
    }

    private sealed class FakeStarter(Action? start) : IEngineStarter
    {
        public int Starts { get; private set; }

        public bool Start()
        {
            if (start is null)
            {
                return false;
            }

            Starts++;
            start();
            return true;
        }
    }
}
