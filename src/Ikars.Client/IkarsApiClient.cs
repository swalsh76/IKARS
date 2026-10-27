using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Ikars.Contracts;

namespace Ikars.Client;

/// <summary>The engine's loopback API (PLAN D3, M1.7): snapshots, subscriptions and the stream. Read-only: nothing here trades.</summary>
public interface IIkarsApi
{
    /// <summary>The version handshake.</summary>
    Task<Hello> HelloAsync(CancellationToken cancellationToken = default);

    /// <summary>The engine's health.</summary>
    Task<HealthView> HealthAsync(CancellationToken cancellationToken = default);

    /// <summary>Markets from the engine's index, by volume.</summary>
    Task<MarketPage> MarketsAsync(string? query, string? category, int offset, int limit, CancellationToken cancellationToken = default);

    /// <summary>One market.</summary>
    Task<MarketView> MarketAsync(string ticker, CancellationToken cancellationToken = default);

    /// <summary>A watched market's order book.</summary>
    Task<BookView> BookAsync(string ticker, CancellationToken cancellationToken = default);

    /// <summary>A market's price history.</summary>
    Task<CandlePage> CandlesAsync(string ticker, int intervalMinutes, int hours, CancellationToken cancellationToken = default);

    /// <summary>The portfolio.</summary>
    Task<PortfolioView> PortfolioAsync(CancellationToken cancellationToken = default);

    /// <summary>The ledger's status.</summary>
    Task<LedgerStatusView> LedgerStatusAsync(CancellationToken cancellationToken = default);

    /// <summary>Ledger records, newest first; of one market when <paramref name="ticker"/> is given.</summary>
    Task<LedgerPage> LedgerRecordsAsync(long? before, int limit, string? ticker = null, CancellationToken cancellationToken = default);

    /// <summary>The alert log: after a line, or the newest.</summary>
    Task<AlertPage> AlertsAsync(long? after, int limit, CancellationToken cancellationToken = default);

    /// <summary>Recommendations (from M1.12).</summary>
    Task<RecommendationPage> RecommendationsAsync(CancellationToken cancellationToken = default);

    /// <summary>Sets which markets a stream carries.</summary>
    Task SetSubscriptionsAsync(string streamId, Subscriptions subscriptions, CancellationToken cancellationToken = default);

    /// <summary>Opens the stream, from just after alert line <paramref name="afterAlert"/> when reconnecting.</summary>
    IAsyncEnumerable<StreamEvent> StreamAsync(long? afterAlert, CancellationToken cancellationToken = default);
}

/// <summary>
/// <see cref="IIkarsApi"/> over HTTP to <c>127.0.0.1</c>, with the session's token on every request. Every client shares one connection pool, so a client made for
/// each reconnect costs nothing to drop. A request that runs past <see cref="RequestTimeout"/> is the engine not answering.
/// </summary>
public sealed class IkarsApiClient : IIkarsApi, IDisposable
{
    private static readonly SocketsHttpHandler SharedHandler = new() { PooledConnectionLifetime = TimeSpan.FromMinutes(2) };
    private readonly HttpClient _http;

    /// <summary>A client for the engine described by <paramref name="info"/>.</summary>
    /// <param name="info">The port and token.</param>
    /// <param name="handler">The HTTP handler (tests pass their own); a new one when omitted.</param>
    public IkarsApiClient(ApiInfo info, HttpMessageHandler? handler = null)
    {
        ArgumentNullException.ThrowIfNull(info);
        _http = new HttpClient(handler ?? SharedHandler, disposeHandler: false);
        _http.BaseAddress = new Uri($"http://127.0.0.1:{info.Port}");
        _http.Timeout = Timeout.InfiniteTimeSpan;   // the stream is long-lived; requests carry their own timeout
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", info.Token);
    }

    /// <summary>How long a snapshot request may take.</summary>
    public TimeSpan RequestTimeout { get; init; } = TimeSpan.FromSeconds(30);

    /// <inheritdoc/>
    public Task<Hello> HelloAsync(CancellationToken cancellationToken = default) => GetAsync<Hello>("/v1/hello", cancellationToken);

    /// <inheritdoc/>
    public Task<HealthView> HealthAsync(CancellationToken cancellationToken = default) => GetAsync<HealthView>("/v1/health", cancellationToken);

    /// <inheritdoc/>
    public Task<MarketPage> MarketsAsync(string? query, string? category, int offset, int limit, CancellationToken cancellationToken = default)
    {
        var path = $"/v1/markets?offset={offset}&limit={limit}";
        if (!string.IsNullOrWhiteSpace(query))
        {
            path += "&q=" + Uri.EscapeDataString(query.Trim());
        }

        if (!string.IsNullOrWhiteSpace(category))
        {
            path += "&category=" + Uri.EscapeDataString(category);
        }

        return GetAsync<MarketPage>(path, cancellationToken);
    }

    /// <inheritdoc/>
    public Task<MarketView> MarketAsync(string ticker, CancellationToken cancellationToken = default) =>
        GetAsync<MarketView>($"/v1/markets/{Uri.EscapeDataString(ticker)}", cancellationToken);

    /// <inheritdoc/>
    public Task<BookView> BookAsync(string ticker, CancellationToken cancellationToken = default) =>
        GetAsync<BookView>($"/v1/books/{Uri.EscapeDataString(ticker)}", cancellationToken);

    /// <inheritdoc/>
    public Task<CandlePage> CandlesAsync(string ticker, int intervalMinutes, int hours, CancellationToken cancellationToken = default) =>
        GetAsync<CandlePage>($"/v1/markets/{Uri.EscapeDataString(ticker)}/candles?interval={intervalMinutes}&hours={hours}", cancellationToken);

    /// <inheritdoc/>
    public Task<PortfolioView> PortfolioAsync(CancellationToken cancellationToken = default) => GetAsync<PortfolioView>("/v1/portfolio", cancellationToken);

    /// <inheritdoc/>
    public Task<LedgerStatusView> LedgerStatusAsync(CancellationToken cancellationToken = default) =>
        GetAsync<LedgerStatusView>("/v1/ledger/status", cancellationToken);

    /// <inheritdoc/>
    public Task<LedgerPage> LedgerRecordsAsync(long? before, int limit, string? ticker = null, CancellationToken cancellationToken = default)
    {
        var path = $"/v1/ledger/records?limit={limit}";
        if (before is { } older)
        {
            path += $"&before={older}";
        }

        if (!string.IsNullOrEmpty(ticker))
        {
            path += "&ticker=" + Uri.EscapeDataString(ticker);
        }

        return GetAsync<LedgerPage>(path, cancellationToken);
    }

    /// <inheritdoc/>
    public Task<AlertPage> AlertsAsync(long? after, int limit, CancellationToken cancellationToken = default) =>
        GetAsync<AlertPage>(after is { } line ? $"/v1/alerts?after={line}&limit={limit}" : $"/v1/alerts?limit={limit}", cancellationToken);

    /// <inheritdoc/>
    public Task<RecommendationPage> RecommendationsAsync(CancellationToken cancellationToken = default) =>
        GetAsync<RecommendationPage>("/v1/recommendations", cancellationToken);

    /// <inheritdoc/>
    public async Task SetSubscriptionsAsync(string streamId, Subscriptions subscriptions, CancellationToken cancellationToken = default)
    {
        using var timeout = Linked(cancellationToken);
        using var content = JsonContent.Create(subscriptions, options: ContractJson.Options);
        await TimedAsync(async () =>
        {
            using var response = await Send(() => _http.PutAsync($"/v1/stream/{Uri.EscapeDataString(streamId)}/subscriptions", content, timeout.Token)).ConfigureAwait(false);
            await EnsureAsync(response, timeout.Token).ConfigureAwait(false);
            return true;
        }, cancellationToken).ConfigureAwait(false);
    }

    /// <inheritdoc/>
    public async IAsyncEnumerable<StreamEvent> StreamAsync(long? afterAlert, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, afterAlert is { } line ? $"/v1/stream?after_alert={line}" : "/v1/stream");
        using var response = await Send(() => _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)).ConfigureAwait(false);
        await EnsureAsync(response, cancellationToken).ConfigureAwait(false);
        using var reader = new StreamReader(await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false));
        await foreach (var (type, _, data) in ServerSentEvents.ReadAsync(reader, cancellationToken).ConfigureAwait(false))
        {
            if (StreamEvent.Parse(type, data) is { } parsed)
            {
                yield return parsed;
            }
        }
    }

    /// <inheritdoc/>
    public void Dispose() => _http.Dispose();

    private async Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
    {
        using var timeout = Linked(cancellationToken);
        return await TimedAsync(async () =>
        {
            using var response = await Send(() => _http.GetAsync(path, timeout.Token)).ConfigureAwait(false);
            await EnsureAsync(response, timeout.Token).ConfigureAwait(false);
            var value = await response.Content.ReadFromJsonAsync<T>(ContractJson.Options, timeout.Token).ConfigureAwait(false);
            return value ?? throw new JsonException($"{path} answered null");
        }, cancellationToken).ConfigureAwait(false);
    }

    private async Task<T> TimedAsync<T>(Func<Task<T>> work, CancellationToken cancellationToken)
    {
        try
        {
            return await work().ConfigureAwait(false);
        }
        catch (OperationCanceledException ex) when (!cancellationToken.IsCancellationRequested)
        {
            // our own time limit, not the caller's cancellation: the engine is not answering
            throw new EngineUnavailableException($"the engine did not answer within {RequestTimeout.TotalSeconds:0} s", ex);
        }
    }

    private CancellationTokenSource Linked(CancellationToken cancellationToken)
    {
        var source = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        source.CancelAfter(RequestTimeout);
        return source;
    }

    private static async Task<HttpResponseMessage> Send(Func<Task<HttpResponseMessage>> send)
    {
        try
        {
            return await send().ConfigureAwait(false);
        }
        catch (HttpRequestException ex)
        {
            throw new EngineUnavailableException("the engine did not answer: " + ex.Message, ex);
        }
    }

    private static async Task EnsureAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        ApiError? error = null;
        try
        {
            error = await response.Content.ReadFromJsonAsync<ApiError>(ContractJson.Options, cancellationToken).ConfigureAwait(false);
        }
        catch (JsonException)
        {
            // not an ApiError: described by its status below
        }

        throw new ApiException((int)response.StatusCode, error?.Error ?? "http_error", error?.Detail ?? $"the engine answered {(int)response.StatusCode}");
    }
}
