using Ikars.Client;
using Ikars.Contracts;
using Ikars.Terminal.Live;
using Ikars.Terminal.Mvvm;
using Ikars.Terminal.Navigation;
using Ikars.Terminal.Screens;

namespace Ikars.Terminal.Shell;

/// <summary>
/// The engine as the screens use it: when a call finds the engine gone or its token stale (the engine restarted: a new port and token), it reconnects once through
/// the connector and tries again.
/// </summary>
internal sealed class ReconnectingApi(Func<CancellationToken, Task<IIkarsApi>> connect, IIkarsApi first) : IIkarsApi
{
    private IIkarsApi _current = first;

    public Task<Hello> HelloAsync(CancellationToken cancellationToken = default) => Call(api => api.HelloAsync(cancellationToken), cancellationToken);

    public Task<HealthView> HealthAsync(CancellationToken cancellationToken = default) => Call(api => api.HealthAsync(cancellationToken), cancellationToken);

    public Task<MarketPage> MarketsAsync(string? query, string? category, int offset, int limit, CancellationToken cancellationToken = default) =>
        Call(api => api.MarketsAsync(query, category, offset, limit, cancellationToken), cancellationToken);

    public Task<MarketView> MarketAsync(string ticker, CancellationToken cancellationToken = default) => Call(api => api.MarketAsync(ticker, cancellationToken), cancellationToken);

    public Task<BookView> BookAsync(string ticker, CancellationToken cancellationToken = default) => Call(api => api.BookAsync(ticker, cancellationToken), cancellationToken);

    public Task<CandlePage> CandlesAsync(string ticker, int intervalMinutes, int hours, CancellationToken cancellationToken = default) =>
        Call(api => api.CandlesAsync(ticker, intervalMinutes, hours, cancellationToken), cancellationToken);

    public Task<PortfolioView> PortfolioAsync(CancellationToken cancellationToken = default) => Call(api => api.PortfolioAsync(cancellationToken), cancellationToken);

    public Task<LedgerStatusView> LedgerStatusAsync(CancellationToken cancellationToken = default) => Call(api => api.LedgerStatusAsync(cancellationToken), cancellationToken);

    public Task<LedgerPage> LedgerRecordsAsync(long? before, int limit, string? ticker = null, CancellationToken cancellationToken = default) =>
        Call(api => api.LedgerRecordsAsync(before, limit, ticker, cancellationToken), cancellationToken);

    public Task<AlertPage> AlertsAsync(long? after, int limit, CancellationToken cancellationToken = default) => Call(api => api.AlertsAsync(after, limit, cancellationToken), cancellationToken);

    public Task<RecommendationPage> RecommendationsAsync(CancellationToken cancellationToken = default) => Call(api => api.RecommendationsAsync(cancellationToken), cancellationToken);

    public Task<GapPage> GapsAsync(int days, CancellationToken cancellationToken = default) => Call(api => api.GapsAsync(days, cancellationToken), cancellationToken);

    public Task SetSubscriptionsAsync(string streamId, Subscriptions subscriptions, CancellationToken cancellationToken = default) =>
        _current.SetSubscriptionsAsync(streamId, subscriptions, cancellationToken);

    public IAsyncEnumerable<StreamEvent> StreamAsync(long? afterAlert, CancellationToken cancellationToken = default) => _current.StreamAsync(afterAlert, cancellationToken);

    private async Task<T> Call<T>(Func<IIkarsApi, Task<T>> call, CancellationToken cancellationToken)
    {
        try
        {
            return await call(_current).ConfigureAwait(true);
        }
        catch (Exception ex) when (ex is EngineUnavailableException or ApiException { Status: 401 })
        {
            _current = await connect(cancellationToken).ConfigureAwait(true);   // the engine restarted: its new port and token
            return await call(_current).ConfigureAwait(true);
        }
    }
}

/// <summary>
/// The shell (PLAN M1.8): connects to the engine (starting it when none runs), keeps the stream going, flushes the hub at a fixed rate, and holds the navigator
/// whose current screen the main window shows.
/// </summary>
internal sealed class ShellViewModel : ObservableObject, IDisposable
{
    private readonly EngineConnector _connector;
    private readonly FrameMeter _frames;
    private readonly CancellationTokenSource _stop = new();
    private string _connection = "starting";
    private string _problem = string.Empty;
    private UpdateHub? _hub;

    public ShellViewModel(EngineConnector connector, FrameMeter frames)
    {
        _connector = connector;
        _frames = frames;
        Navigator = new Navigator(Build, ex => Problem = ex.Message);
    }

    public Navigator Navigator { get; }

    /// <summary>The menu: the top-level screens.</summary>
    public IReadOnlyList<Ref> Menu { get; } = [Ref.Overview, Ref.Markets, Ref.Ledger, Ref.Alerts, Ref.Health];

    public string Connection { get => _connection; private set => Set(ref _connection, value); }

    public string Problem { get => _problem; set => Set(ref _problem, value); }

    public string FrameTime => _frames.Text;

    /// <summary>The frame meter the window feeds.</summary>
    public FrameMeter Frames => _frames;

    internal ScreenContext? Context { get; private set; }

    /// <summary>Connects (or starts the engine), opens the stream, and shows the Overview.</summary>
    public async Task StartAsync()
    {
        var first = await _connector.ConnectOrStartAsync(line => Connection = line, _stop.Token).ConfigureAwait(true);
        var api = new ReconnectingApi(_connector.ConnectAsync, first);
        var watcher = new StreamWatcher(_connector.ConnectAsync);
        _hub = new UpdateHub(wanted => watcher.SetWantedAsync(wanted, _stop.Token));
        Context = new ScreenContext(api, _hub, Navigator, new Selection(), _frames, message => Problem = message);
        _ = Task.Run(() => watcher.RunAsync(_hub.Receive, _hub.ReceiveNotice, _stop.Token));
        Connection = "connected";
        await Navigator.GoAsync(Ref.Overview).ConfigureAwait(true);
    }

    /// <summary>About ten times a second, on the UI thread: hands the stream's changes to the screens.</summary>
    public async Task TickAsync()
    {
        if (_hub is null)
        {
            return;
        }

        try
        {
            await _hub.Flush().ConfigureAwait(true);
        }
        catch (Exception ex)
        {
            // The tick runs from a timer: a failure is shown, never thrown; a cancel at stop is not a failure.
            if (ex is not OperationCanceledException)
            {
                Problem = ex.Message;
            }
        }

        Connection = _hub.Connection;
        Raise(nameof(FrameTime));
    }

    public void Stop() => _stop.Cancel();

    public void Dispose()
    {
        Navigator.Dispose();
        _stop.Dispose();
    }

    internal Screen Build(Ref reference)
    {
        var context = Context ?? throw new InvalidOperationException("not connected");
        return reference.Kind switch
        {
            "overview" => new OverviewScreen(context),
            "markets" => new MarketsScreen(context),
            "market" => new MarketScreen(context, reference.Id),
            "ledger" => new LedgerScreen(context),
            "alerts" => new AlertsScreen(context),
            "health" => new HealthScreen(context),
            _ => throw new ArgumentException($"no screen for {reference.Kind}", nameof(reference)),
        };
    }
}
