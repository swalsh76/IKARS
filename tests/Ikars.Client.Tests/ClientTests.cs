using System.Net;
using System.Text;
using Ikars.Contracts;

namespace Ikars.Client.Tests;

/// <summary>An HTTP handler that answers from a function and remembers the requests (bodies read eagerly).</summary>
internal sealed class FakeHandler(Func<HttpRequestMessage, HttpResponseMessage> answer) : HttpMessageHandler
{
    public List<(HttpRequestMessage Request, string? Body)> Seen { get; } = [];

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var body = request.Content is null ? null : await request.Content.ReadAsStringAsync(cancellationToken);
        Seen.Add((request, body));
        return answer(request);
    }

    public static HttpResponseMessage Json(string json, HttpStatusCode status = HttpStatusCode.OK) =>
        new(status) { Content = new StringContent(json, Encoding.UTF8, "application/json") };
}

public class ClientTests
{
    [Fact]
    public void ATimeoutIsToldApartFromAnEngineThatIsGone()
    {
        Assert.True(new EngineUnavailableException("slow", new OperationCanceledException()).TimedOut);
        Assert.False(new EngineUnavailableException("gone", new HttpRequestException("refused")).TimedOut);
        Assert.False(new EngineUnavailableException("gone").TimedOut);
    }

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static readonly ApiInfo Info = new(5555, "made-up-token", "1.0");

    [Fact]
    public async Task SnapshotsCarryTheTokenAndDecodeTheContracts()
    {
        using var handler = new FakeHandler(_ => FakeHandler.Json("""{"api_version":"1.0","engine_version":"0.0.0","schema_sha256":"x"}"""));
        using var client = new IkarsApiClient(Info, handler);
        var hello = await client.HelloAsync(cancellationToken: Ct);
        Assert.Equal("1.0", hello.ApiVersion);
        var (request, _) = handler.Seen.Single();
        Assert.Equal("Bearer made-up-token", request.Headers.Authorization?.ToString());
        Assert.Equal("http://127.0.0.1:5555/v1/hello", request.RequestUri?.ToString());
    }

    [Fact]
    public async Task QueriesAreBuiltAndEscaped()
    {
        using var handler = new FakeHandler(request => request.RequestUri!.AbsolutePath switch
        {
            "/v1/markets" => FakeHandler.Json("""{"items":[],"total":0,"offset":5}"""),
            "/v1/ledger/records" => FakeHandler.Json("""{"items":[],"head_seq":0,"before":null}"""),
            "/v1/alerts" => FakeHandler.Json("""{"items":[]}"""),
            "/v1/markets/A%20B/candles" => FakeHandler.Json("""{"ticker":"A B","interval_minutes":1,"items":[]}"""),
            _ => FakeHandler.Json("{}", HttpStatusCode.NotFound),
        });
        using var client = new IkarsApiClient(Info, handler);
        await client.MarketsAsync(" rain & snow ", "Weather", 5, 50, cancellationToken: Ct);
        await client.MarketsAsync(null, null, 0, 10, cancellationToken: Ct);
        await client.LedgerRecordsAsync(9, 20, "KXT-1", cancellationToken: Ct);
        await client.LedgerRecordsAsync(null, 20, cancellationToken: Ct);
        await client.AlertsAsync(3, 10, cancellationToken: Ct);
        await client.AlertsAsync(null, 10, cancellationToken: Ct);
        await client.CandlesAsync("A B", 1, 24, cancellationToken: Ct);
        var paths = handler.Seen.Select(seen => seen.Request.RequestUri!.PathAndQuery).ToList();
        Assert.Equal(
            [
                "/v1/markets?offset=5&limit=50&q=rain%20%26%20snow&category=Weather",
                "/v1/markets?offset=0&limit=10",
                "/v1/ledger/records?limit=20&before=9&ticker=KXT-1",
                "/v1/ledger/records?limit=20",
                "/v1/alerts?after=3&limit=10",
                "/v1/alerts?limit=10",
                "/v1/markets/A%20B/candles?interval=1&hours=24",
            ],
            paths);
    }

    [Fact]
    public async Task EveryOtherSnapshotRouteDecodes()
    {
        var examples = Path.Combine(FindContracts(), "examples");
        string Example(string name) => File.ReadAllText(Path.Combine(examples, name + ".json"));
        using var handler = new FakeHandler(request => FakeHandler.Json(request.RequestUri!.AbsolutePath switch
        {
            "/v1/health" => Example("HealthView"),
            "/v1/markets/KXA" => Example("MarketView"),
            "/v1/books/KXA" => Example("BookView"),
            "/v1/portfolio" => Example("PortfolioView"),
            "/v1/ledger/status" => Example("LedgerStatusView"),
            "/v1/recommendations" => Example("RecommendationPage"),
            "/v1/gaps" => Example("GapPage"),
            _ => "{}",
        }));
        using var client = new IkarsApiClient(Info, handler);
        Assert.NotEmpty((await client.HealthAsync(cancellationToken: Ct)).Parts);
        Assert.True((await client.MarketAsync("KXA", cancellationToken: Ct)).Watched);
        Assert.NotEmpty((await client.BookAsync("KXA", cancellationToken: Ct)).Yes);
        Assert.NotEmpty((await client.PortfolioAsync(cancellationToken: Ct)).Positions);
        Assert.Equal("America/Denver", (await client.LedgerStatusAsync(cancellationToken: Ct)).TaxZone);
        Assert.Empty((await client.RecommendationsAsync(cancellationToken: Ct)).Items);
        Assert.Equal("trade", (await client.GapsAsync(7, cancellationToken: Ct)).Items[0].Channel);
    }

    [Fact]
    public async Task RefusalsCarryTheApisOwnCode()
    {
        using var handler = new FakeHandler(_ => FakeHandler.Json("""{"error":"not_found","detail":"no such market"}""", HttpStatusCode.NotFound));
        using var client = new IkarsApiClient(Info, handler);
        var refused = await Assert.ThrowsAsync<ApiException>(() => client.MarketAsync("NOPE", cancellationToken: Ct));
        Assert.Equal((404, "not_found", "no such market"), (refused.Status, refused.Code, refused.Detail));
        using var bare = new FakeHandler(_ => new HttpResponseMessage(HttpStatusCode.BadGateway) { Content = new StringContent("oops") });
        using var plain = new IkarsApiClient(Info, bare);
        Assert.Equal("http_error", (await Assert.ThrowsAsync<ApiException>(() => plain.HealthAsync(cancellationToken: Ct))).Code);
    }

    [Fact]
    public async Task AnEngineThatDoesNotAnswerIsUnavailable()
    {
        using var handler = new FakeHandler(_ => throw new HttpRequestException("connection refused"));
        using var client = new IkarsApiClient(Info, handler);
        await Assert.ThrowsAsync<EngineUnavailableException>(() => client.HelloAsync(cancellationToken: Ct));
    }

    [Fact]
    public async Task SubscriptionsArePutAsJson()
    {
        using var handler = new FakeHandler(_ => FakeHandler.Json("""{"quotes":["A"],"books":[]}"""));
        using var client = new IkarsApiClient(Info, handler);
        await client.SetSubscriptionsAsync("s 1", new Subscriptions(["A"], []), Ct);
        var (request, body) = handler.Seen.Single();
        Assert.Equal(HttpMethod.Put, request.Method);
        Assert.Equal("/v1/stream/s%201/subscriptions", request.RequestUri!.AbsolutePath);
        Assert.Equal("""{"quotes":["A"],"books":[]}""", body);
    }

    [Fact]
    public async Task TheStreamIsParsedAndUnknownEventsAreSkipped()
    {
        const string text = """
            : a comment
            id: 1
            event: opened
            data: {"stream_id":"s1","coalesce_ms":100}

            event: future_thing
            data: {"x":1}

            event: quote
            data: {"ticker":"A","yes_bid_e6":410000,"yes_ask_e6":null,
            data: "last_price_e6":null,"received_ns":5}

            event: alert
            data: {"line":7,"key":"k","priority":"high","title":"t","text":"x","at":"a","outcome":"shown"}


            """;
        using var handler = new FakeHandler(_ => new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(text) });
        using var client = new IkarsApiClient(Info, handler);
        var events = new List<StreamEvent>();
        await foreach (var item in client.StreamAsync(6, cancellationToken: Ct))
        {
            events.Add(item);
        }

        Assert.Equal("/v1/stream?after_alert=6", handler.Seen.Single().Request.RequestUri!.PathAndQuery);
        Assert.Collection(
            events,
            e => Assert.Equal("s1", Assert.IsType<StreamEvent.Opened>(e).Value.StreamId),
            e => Assert.Equal(410000, Assert.IsType<StreamEvent.Quote>(e).Value.YesBidE6),
            e => Assert.Equal(7, Assert.IsType<StreamEvent.Alert>(e).Value.Line));
        Assert.Null(StreamEvent.Parse("nope", "{}"));
        Assert.IsType<StreamEvent.Refused>(StreamEvent.Parse("error", """{"error":"unavailable","detail":"busy"}"""));
        Assert.IsType<StreamEvent.Health>(StreamEvent.Parse("health", """{"at_utc":"x","parts":{}}"""));
        Assert.IsType<StreamEvent.Book>(StreamEvent.Parse("book", """{"ticker":"A","ready":true,"yes":[],"no":[]}"""));
        Assert.IsType<StreamEvent.Portfolio>(StreamEvent.Parse("portfolio", File.ReadAllText(Path.Combine(FindContracts(), "examples", "PortfolioView.json"))));
    }

    internal static string FindContracts()
    {
        for (var folder = new DirectoryInfo(AppContext.BaseDirectory); folder is not null; folder = folder.Parent)
        {
            var candidate = Path.Combine(folder.FullName, "contracts");
            if (File.Exists(Path.Combine(candidate, "ikars-api.schema.json")))
            {
                return candidate;
            }
        }

        throw new InvalidOperationException("contracts folder not found");
    }
}

public class ClientTimeoutTests
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    [Fact]
    public async Task ARequestPastTheTimeLimitIsTheEngineNotAnswering()
    {
        using var handler = new SlowHandler();
        using var client = new IkarsApiClient(new ApiInfo(5555, "t", "1.0"), handler) { RequestTimeout = TimeSpan.FromMilliseconds(50) };
        var slow = await Assert.ThrowsAsync<EngineUnavailableException>(() => client.HelloAsync(Ct));
        Assert.Contains("did not answer", slow.Message, StringComparison.Ordinal);
        await Assert.ThrowsAsync<EngineUnavailableException>(() => client.SetSubscriptionsAsync("s", new Ikars.Contracts.Subscriptions([], []), Ct));
    }

    private sealed class SlowHandler : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            await Task.Delay(Timeout.Infinite, cancellationToken);
            return new HttpResponseMessage();
        }
    }
}
