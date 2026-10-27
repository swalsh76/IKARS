using System.IO;
using Ikars.Client;
using Ikars.Contracts;
using Ikars.Terminal.Live;
using Ikars.Terminal.Mvvm;
using Ikars.Terminal.Navigation;
using Ikars.Terminal.Screens;
using Ikars.Terminal.Shell;

namespace Ikars.Terminal.Tests;

public class NavigationTests
{
    private sealed class FakeScreen(Ref reference, Exception? failOpen = null) : Screen(reference)
    {
        public int Opened { get; private set; }

        public int Closed { get; private set; }

        public override Task OpenAsync(CancellationToken cancellationToken)
        {
            Opened++;
            return failOpen is null ? Task.CompletedTask : Task.FromException(failOpen);
        }

        public override void Close() => Closed++;
    }

    [Fact]
    public void ReferencesNameThemselvesAndTheirTrail()
    {
        var market = Ref.Market("KXA");
        Assert.Equal("KXA", market.Label);
        Assert.Equal([Ref.Overview, Ref.Markets, market], market.Trail);
        Assert.Equal([Ref.Overview], Ref.Overview.Trail);
        Assert.Equal([Ref.Overview, Ref.Ledger], Ref.Ledger.Trail);
        Assert.Equal(["Overview", "Markets", "Ledger", "Alerts", "Health"], new[] { Ref.Overview, Ref.Markets, Ref.Ledger, Ref.Alerts, Ref.Health }.Select(r => r.Label));
    }

    [Fact]
    public async Task DrillDownBackForwardAndBreadcrumbs()
    {
        var built = new List<FakeScreen>();
        var navigator = new Navigator(r =>
        {
            var screen = new FakeScreen(r);
            built.Add(screen);
            return screen;
        }, _ => { });
        await navigator.GoAsync(Ref.Overview);
        await navigator.GoAsync(Ref.Markets);
        await navigator.GoAsync(Ref.Market("KXA"));
        await navigator.GoAsync(Ref.Market("KXA"));   // already there: nothing
        Assert.Equal(3, built.Count);
        Assert.Equal(["Overview", "Markets", "KXA"], navigator.Breadcrumbs.Select(b => b.Label));
        Assert.True(navigator.CanGoBack && !navigator.CanGoForward && navigator.BackCommand.CanExecute(null));
        await navigator.GoBackAsync();
        Assert.Equal(Ref.Markets, navigator.Current?.Reference);
        Assert.Equal(1, built[2].Closed);   // the market let go of its subscriptions
        Assert.True(navigator.CanGoForward && navigator.ForwardCommand.CanExecute(null));
        await navigator.GoForwardAsync();
        Assert.Equal(Ref.Market("KXA"), navigator.Current?.Reference);
        await navigator.GoBackAsync();
        await navigator.GoAsync(Ref.Health);   // a new path drops the forward history
        Assert.False(navigator.CanGoForward);
        await navigator.GoBackAsync();
        await navigator.GoBackAsync();
        await navigator.GoBackAsync();   // at the start: nothing
        Assert.Equal(Ref.Overview, navigator.Current?.Reference);
        var fresh = new Navigator(r => new FakeScreen(r), _ => { });
        await fresh.GoForwardAsync();
        Assert.Null(fresh.Current);
    }

    private sealed class SlowScreen(Ref reference, TaskCompletionSource loaded) : Screen(reference)
    {
        public bool Subscribed { get; private set; }

        public override async Task OpenAsync(CancellationToken cancellationToken)
        {
            await loaded.Task.WaitAsync(cancellationToken);
            Subscribed = true;
        }

        public override void Close() => Subscribed = false;
    }

    [Fact]
    public async Task AScreenLeftMidLoadIsCancelledAndLetsGo()
    {
        var loaded = new TaskCompletionSource();
        var problems = new List<Exception>();
        var slow = new SlowScreen(Ref.Ledger, loaded);
        var finishing = new SlowScreen(Ref.Alerts, loaded);
        using var navigator = new Navigator(r => r == Ref.Ledger ? slow : r == Ref.Alerts ? finishing : new FakeScreen(r), problems.Add);
        var opening = navigator.GoAsync(Ref.Ledger);
        await navigator.GoAsync(Ref.Health);
        await opening;
        Assert.False(slow.Subscribed);
        Assert.Empty(problems);   // a cancelled load is not a problem

        var second = navigator.GoAsync(Ref.Alerts);
        Assert.Same(finishing, navigator.Current);
        loaded.SetResult();
        await second;
        Assert.True(finishing.Subscribed);   // still shown: keeps what it loaded
    }

    [Fact]
    public async Task AFailingScreenIsReportedNotThrown()
    {
        var problems = new List<Exception>();
        var navigator = new Navigator(r => new FakeScreen(r, new InvalidOperationException("engine gone")), problems.Add);
        await navigator.GoAsync(Ref.Overview);
        Assert.Equal("engine gone", Assert.Single(problems).Message);
        navigator.GoCommand.Execute(Ref.Health);
        Assert.False(navigator.GoCommand.CanExecute("not a ref"));
        navigator.GoCommand.Execute("not a ref");   // ignored
    }
}

public class HubTests
{
    private static QuoteUpdate Quote(string ticker, long bid) => new(ticker, bid, 430_000, 420_000, 1);

    [Fact]
    public async Task ABurstIsHandedOverOnceAndSubscriptionsFollowTheScreens()
    {
        var applied = new List<Subscriptions>();
        var hub = new UpdateHub(wanted =>
        {
            applied.Add(wanted);
            return Task.CompletedTask;
        });
        var quotes = new List<QuoteUpdate>();
        var books = new List<BookView>();
        var portfolios = new List<PortfolioView>();
        var healths = new List<HealthView>();
        var alerts = new List<AlertView>();
        var notices = new List<string>();
        hub.Quote += quotes.Add;
        hub.Book += books.Add;
        hub.Portfolio += portfolios.Add;
        hub.Health += healths.Add;
        hub.Alert += alerts.Add;
        hub.Notice += notices.Add;
        for (var n = 0; n < 800; n++)
        {
            hub.Receive(new StreamEvent.Quote(Quote("A", 400_000 + n)));   // a burst on one market
        }

        hub.Receive(new StreamEvent.Book(new BookView("A", true, [], [])));
        hub.Receive(new StreamEvent.Book(new BookView("A", true, [new Level(1, 1)], [])));
        hub.Receive(new StreamEvent.Portfolio(new FakeEngine().Portfolio));
        hub.Receive(new StreamEvent.Health(new FakeEngine().Health));
        hub.Receive(new StreamEvent.Alert(new FakeEngine().Alerts[0]));
        hub.Receive(new StreamEvent.Alert(new FakeEngine().Alerts[1]));
        hub.Receive(new StreamEvent.Opened(new StreamOpened("s", 100)));   // not for the screens
        hub.ReceiveNotice("connected");
        object first = new(), second = new();
        hub.Want(first, ["B", "A"], ["A"]);
        hub.Want(second, ["A", "C"], []);
        await hub.Flush();
        Assert.Equal(400_799, Assert.Single(quotes).YesBidE6);   // only the latest
        Assert.Single(Assert.Single(books).Yes);
        Assert.Single(portfolios);
        Assert.Single(healths);
        Assert.Equal(2, alerts.Count);
        Assert.Equal("connected", hub.Connection);
        Assert.Equal(["A", "B", "C"], Assert.Single(applied).Quotes);
        await hub.Flush();   // nothing new, screens unchanged
        Assert.Single(quotes);
        Assert.Single(applied);
        hub.Release(first);
        hub.Release(first);   // twice: harmless
        await hub.Flush();
        Assert.Equal(["A", "C"], applied[^1].Quotes);
        Assert.Empty(applied[^1].Books);
    }
}

public class HubRetryTests
{
    [Fact]
    public async Task AFailedSubscriptionChangeIsTriedAgain()
    {
        var calls = 0;
        var hub = new UpdateHub(_ => ++calls == 1 ? throw new EngineUnavailableException("gone") : Task.CompletedTask);
        hub.Want(new object(), ["A"], []);
        await Assert.ThrowsAsync<EngineUnavailableException>(hub.Flush);
        await hub.Flush();
        Assert.Equal(2, calls);
        await hub.Flush();
        Assert.Equal(2, calls);
    }

    [Fact]
    public async Task TheSameMarketsAgainAreNotSentAgain()
    {
        var calls = 0;
        var hub = new UpdateHub(_ =>
        {
            calls++;
            return Task.CompletedTask;
        });
        var overview = new object();
        hub.Want(overview, ["A", "B"], []);
        await hub.Flush();
        hub.Want(overview, ["A", "B"], []);   // the portfolio again, a second later
        await hub.Flush();
        Assert.Equal(1, calls);
        hub.Want(overview, ["A"], []);
        await hub.Flush();
        Assert.Equal(2, calls);
    }
}

public class FormattingTests
{
    [Fact]
    public void MoneyAndCountsStayExact()
    {
        Assert.Equal("$1,234.56", Formatting.Dollars(1_234_560_000));
        Assert.Equal("$0.42", Formatting.Dollars(420_000));
        Assert.Equal("-$0.80", Formatting.Dollars(-800_000));
        Assert.Equal("—", Formatting.Dollars(null));
        Assert.Equal("+$0.07", Formatting.Signed(72_000));
        Assert.Equal("-$0.80", Formatting.Signed(-800_000));
        Assert.Equal("—", Formatting.Signed(null));
        Assert.Equal("42¢", Formatting.Cents(420_000));
        Assert.Equal("42.5¢", Formatting.Cents(425_000));
        Assert.Equal("—", Formatting.Cents(null));
        Assert.Equal("12.5", Formatting.Contracts(1_250));
        Assert.Equal("1,000", Formatting.Contracts(100_000));
        Assert.Equal("—", Formatting.Contracts(null));
        Assert.Matches(@"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", Formatting.LocalTime("2026-01-01T12:00:00Z"));
        Assert.Equal("not a time", Formatting.LocalTime("not a time"));
    }
}

public class FrameMeterTests
{
    [Fact]
    public void GapsBetweenFramesGivePercentiles()
    {
        var meter = new FrameMeter();
        Assert.Null(meter.P99);
        Assert.Equal("frame time: measuring", meter.Text);
        Assert.False(meter.WithinTarget);
        var at = TimeSpan.Zero;
        for (var n = 0; n < 99; n++)
        {
            at += TimeSpan.FromMilliseconds(16);
            meter.Frame(at);
            meter.Frame(at);   // WPF repeats a time when it skips: not a frame
        }

        for (var n = 0; n < 2; n++)
        {
            at += TimeSpan.FromMilliseconds(100);   // two slow frames in a hundred
            meter.Frame(at);
        }

        Assert.Equal(100, meter.Count);
        Assert.Equal(16, meter.P50);
        Assert.Equal(100, meter.P99);
        Assert.False(meter.WithinTarget);
        for (var n = 0; n < FrameMeter.Window; n++)
        {
            at += TimeSpan.FromMilliseconds(16);
            meter.Frame(at);
        }

        Assert.Equal(FrameMeter.Window, meter.Count);
        Assert.True(meter.WithinTarget);
        Assert.Equal("frame p50 16 ms, p99 16 ms", meter.Text);
        at += TimeSpan.FromSeconds(4);   // minimised, then restored: a pause, not a slow frame
        meter.Frame(at);
        Assert.True(meter.WithinTarget);
    }
}

public class CommandTests
{
    [Fact]
    public async Task AsyncCommandsReportFailuresAndDoNotOverlap()
    {
        var errors = new List<Exception>();
        var gate = new TaskCompletionSource();
        var runs = 0;
        var command = new AsyncCommand(async () =>
        {
            runs++;
            await gate.Task;
        }, errors.Add);
        var first = command.RunAsync();
        Assert.False(command.CanExecute(null));
        await command.RunAsync();   // refused while running
        gate.SetResult();
        await first;
        Assert.Equal(1, runs);
        await new AsyncCommand(() => throw new InvalidOperationException("boom"), errors.Add).RunAsync();
        await new AsyncCommand(() => throw new OperationCanceledException(), errors.Add).RunAsync();
        Assert.Equal("boom", Assert.Single(errors).Message);
        var relay = new RelayCommand(() => runs++, () => false);
        Assert.False(relay.CanExecute(null));
        relay.Execute(null);
        relay.RaiseCanExecuteChanged();
        Assert.Equal(2, runs);
    }
}

public class ScreenTests
{
    private static (ScreenContext Context, FakeEngine Engine, UpdateHub Hub, List<string> Problems) Make()
    {
        var engine = new FakeEngine();
        var hub = new UpdateHub(_ => Task.CompletedTask);
        var problems = new List<string>();
        ScreenContext? context = null;
        var navigator = new Navigator(r => new ShellViewModelProbe(context!).Build(r), ex => problems.Add(ex.Message));
        context = new ScreenContext(engine, hub, navigator, new Selection(), new FrameMeter(), problems.Add);
        return (context, engine, hub, problems);
    }

    private sealed class ShellViewModelProbe(ScreenContext context)
    {
        public Screen Build(Ref reference) => reference.Kind switch
        {
            "market" => new MarketScreen(context, reference.Id),
            "markets" => new MarketsScreen(context),
            _ => new OverviewScreen(context),
        };
    }

    [Fact]
    public async Task TheOverviewShowsPortfolioAlertsHealthAndThePlannedPanels()
    {
        var (ctx, _, hub, _) = Make();
        var overview = new OverviewScreen(ctx);
        await overview.OpenAsync(CancellationToken.None);
        Assert.Equal("$3.00", overview.Cash);
        Assert.Equal("+$0.07", overview.Unrealised);
        Assert.Equal(Ref.Market("KXDEMO-26JAN01-A"), Assert.Single(overview.Positions).Target);
        Assert.Equal("Demo", Assert.Single(overview.Exposure).Label);
        Assert.Equal(8, overview.Alerts.Count);
        Assert.Equal(10, overview.Alerts[0].Line);   // newest first
        Assert.Equal(["Feed", "Ledger", "Backup", "Open gaps", "Frame time"], overview.HealthStrip.Select(f => f.Label));
        Assert.All(overview.HealthStrip, f => Assert.True(f.Good));
        Assert.Equal(6, OverviewScreen.Planned.Count);
        Assert.Contains(OverviewScreen.Planned, p => p.Title == "Top recommendations" && p.ArrivesIn == "M1.12");
        overview.AddAlert(new AlertView(11, "k", "urgent", "t", "new", "a", "shown"));
        Assert.Equal(11, overview.Alerts[0].Line);
        Assert.Equal(8, overview.Alerts.Count);
        overview.ShowPortfolio(new PortfolioView(0, 0, null, [], new Dictionary<string, long>(), new Dictionary<string, long>()));
        Assert.Equal("no open positions", overview.Unrealised);
        overview.ShowPortfolio(new FakeEngine().Portfolio with { UnrealisedE6 = null });
        Assert.Equal("not all priced", overview.Unrealised);
        overview.ShowHealth(new HealthView("x", FakeEngine.Parse("""{"kalshi": {"feed": {"connected": false}, "open_gaps": 2}, "ledger": {"reconciled_exact": false}}""")));
        Assert.Equal(["not connected", "differs from Kalshi", "none yet", "2"], overview.HealthStrip.Take(4).Select(f => f.Value));
        Assert.All(overview.HealthStrip.Take(4), f => Assert.False(f.Good));
        Assert.Null(OverviewScreen.Read(FakeEngine.Parse("{}")));
        Assert.Equal("text", OverviewScreen.Read(FakeEngine.Parse("""{"a": {"b": "text"}}"""), "a", "b"));
        Assert.Null(OverviewScreen.Read(FakeEngine.Parse("""{"a": 5}"""), "a", "b"));
        Assert.Null(OverviewScreen.Read(FakeEngine.Parse("""{"a": null}"""), "a"));
        overview.Close();
        await hub.Flush();
        Assert.Empty(hub.Streaming.Quotes);
    }

    [Fact]
    public async Task MarketsSearchPageStreamAndHighlight()
    {
        var (ctx, engine, hub, _) = Make();
        engine.Markets.AddRange(Enumerable.Range(0, 60).Select(n => FakeEngine.Market($"KXMANY-{n:00}", evt: "KXMANY")));
        var markets = new MarketsScreen(ctx);
        await markets.OpenAsync(CancellationToken.None);
        Assert.Equal(MarketsScreen.PageSize, markets.Rows.Count);
        Assert.Equal("1–50 of 63", markets.Summary);
        Assert.True(markets.Next.CanExecute(null));
        Assert.False(markets.Previous.CanExecute(null));
        await markets.Next.RunAsync();
        Assert.Equal("51–63 of 63", markets.Summary);
        await markets.Previous.RunAsync();
        markets.Query = "KXDEMO";
        markets.Category = "Demo";
        await markets.Search.RunAsync();
        Assert.Equal(["KXDEMO-26JAN01-A", "KXDEMO-26JAN01-B"], markets.Rows.Select(r => r.Ticker));
        await hub.Flush();
        Assert.Equal(["KXDEMO-26JAN01-A", "KXDEMO-26JAN01-B"], hub.Streaming.Quotes);
        hub.Receive(new StreamEvent.Quote(new QuoteUpdate("KXDEMO-26JAN01-A", 450_000, 470_000, null, 1)));
        await hub.Flush();
        Assert.Equal(("45¢", "47¢"), (markets.Rows[0].Bid, markets.Rows[0].Ask));
        ctx.Selection.Current = Ref.Market("KXDEMO-26JAN01-B");
        Assert.True(markets.Rows[1].Selected && !markets.Rows[0].Selected);
        Assert.True(markets.Rows[0].Watched && markets.Rows[0].Title.Length > 0 && markets.Rows[0].Category == "Demo");
        markets.Query = "nothing";
        await markets.Search.RunAsync();
        Assert.Equal("no markets", markets.Summary);
        markets.Query = string.Empty;
        markets.Category = string.Empty;
        await markets.Search.RunAsync();
        engine.MarketsGate = new TaskCompletionSource();
        var late = markets.Next.RunAsync();   // slow, and the owner moves on
        markets.Close();
        engine.MarketsGate.SetResult();
        await late;
        await hub.Flush();
        Assert.Empty(hub.Streaming.Quotes);   // the late page subscribed nothing
    }

    [Fact]
    public async Task AMarketShowsItsQuoteBookChartAndLinks()
    {
        var (ctx, engine, hub, _) = Make();
        var market = new MarketScreen(ctx, "KXDEMO-26JAN01-A");
        await market.OpenAsync(CancellationToken.None);
        Assert.Equal("Made up KXDEMO-26JAN01-A", market.Title);
        Assert.Contains("KXDEMO-26JAN01", market.Details, StringComparison.Ordinal);
        Assert.Equal(("41¢", "43¢", "42¢"), (market.Bid, market.Ask, market.Last));
        Assert.Equal(MarketScreen.BookDepth, market.YesBids.Count);
        Assert.Equal(new BookLevel("41¢", "12.5"), market.YesBids[0]);
        Assert.Equal(string.Empty, market.BookNote);
        Assert.Equal(42.0, Assert.Single(market.ChartPoints).Cents);
        Assert.Equal(1, market.ChartVersion);
        Assert.Equal(string.Empty, market.ChartNote);
        Assert.Equal(Ref.Market("KXDEMO-26JAN01-B"), Assert.Single(market.SameEvent).Target);
        Assert.Equal(20, market.LedgerRecords.Count);
        Assert.Equal(Ref.Market("KXDEMO-26JAN01-A"), ctx.Selection.Current);
        await hub.Flush();
        Assert.Equal(["KXDEMO-26JAN01-A"], hub.Streaming.Books);
        hub.Receive(new StreamEvent.Quote(new QuoteUpdate("OTHER", 1, 1, 1, 1)));
        hub.Receive(new StreamEvent.Book(new BookView("OTHER", true, [], [])));
        hub.Receive(new StreamEvent.Book(new BookView("KXDEMO-26JAN01-A", false, [], [])));
        await hub.Flush();
        Assert.Equal("41¢", market.Bid);   // another market's quote is not this one's
        Assert.Equal("rebuilding (waiting for a snapshot)", market.BookNote);
        market.Close();

        engine.Candles = _ => throw new ApiException(503, "unavailable", "Kalshi down");
        var quiet = new MarketScreen(ctx, "KXOTHER-1");
        engine.Markets[2] = FakeEngine.Market("KXOTHER-1", watched: false, evt: "KXOTHER");
        await quiet.OpenAsync(CancellationToken.None);
        Assert.Equal("the engine keeps books for its watched markets only", quiet.BookNote);
        Assert.Empty(quiet.ChartPoints);
        Assert.Equal("price history unavailable now (Kalshi did not answer)", quiet.ChartNote);
        quiet.Close();
        engine.Candles = _ => throw new ApiException(404, "not_found", "none");
        var unknown = new MarketScreen(ctx, "KXOTHER-1");
        await unknown.OpenAsync(CancellationToken.None);
        Assert.Equal("no price history for this market", unknown.ChartNote);
        unknown.Close();
        engine.Candles = ticker => new CandlePage(ticker, 1, []);
        var untraded = new MarketScreen(ctx, "KXOTHER-1");
        await untraded.OpenAsync(CancellationToken.None);
        Assert.Equal("no trades today", untraded.ChartNote);
        Assert.Empty(quiet.SameEvent);
        Assert.Equal("KXOTHER-1", quiet.Ticker);
    }

    [Fact]
    public async Task TheLedgerPagesNewestFirst()
    {
        var (ctx, _, _, _) = Make();
        var ledger = new LedgerScreen(ctx);
        await ledger.OpenAsync(CancellationToken.None);
        Assert.Contains("150 records", ledger.Status, StringComparison.Ordinal);
        Assert.Contains("chain verified", ledger.Status, StringComparison.Ordinal);
        Assert.Equal("#150 fill", ledger.Records[0].Primary);
        Assert.Equal(Ref.Market("KXDEMO-26JAN01-A"), ledger.Records[0].Target);
        Assert.Equal(Ref.Ledger, ledger.Records[1].Target);
        Assert.True(ledger.Older.CanExecute(null));
        await ledger.Older.RunAsync();
        Assert.Equal("#50 fill", ledger.Records[0].Primary);
        Assert.False(ledger.Older.CanExecute(null));
        await ledger.Newest.RunAsync();
        Assert.Equal("#150 fill", ledger.Records[0].Primary);
    }

    [Fact]
    public async Task AlertsAreGroupedByPriorityAndHealthIsFlattened()
    {
        var (ctx, _, hub, _) = Make();
        var alerts = new AlertsScreen(ctx);
        await alerts.OpenAsync(CancellationToken.None);
        Assert.Equal((3, 4, 3), (alerts.Urgent.Count, alerts.High.Count, alerts.Other.Count));
        hub.Receive(new StreamEvent.Alert(new AlertView(11, "k", "urgent", "t", "x", "a", "shown")));
        await hub.Flush();
        Assert.Equal(11, alerts.Urgent[0].Line);
        alerts.Add(new AlertView(11, "k", "urgent", "t", "x", "a", "shown"));   // also in a snapshot: once
        alerts.Add(new AlertView(7, "k", "urgent", "t", "x", "a", "shown"));    // older: in its place
        Assert.Equal([11, 9, 7, 6, 3], alerts.Urgent.Select(a => a.Line));
        for (var line = 100; line < 400; line++)
        {
            alerts.Add(new AlertView(line, "k", "high", "t", "x", "a", "shown"));
        }

        Assert.Equal(AlertLists.PerPriority, alerts.High.Count);
        Assert.Equal(399, alerts.High[0].Line);
        alerts.Close();

        var health = new HealthScreen(ctx);
        await health.OpenAsync(CancellationToken.None);
        Assert.Contains(health.Rows, f => f.Label == "kalshi.feed.connected" && f.Value == "true");
        Assert.Contains(health.Rows, f => f.Label == "backup.hours_since" && f.Value == "3");
        Assert.Equal("trade", Assert.Single(health.Gaps).Channel);
        Assert.Contains("target p99", health.FrameTime, StringComparison.Ordinal);
        hub.Receive(new StreamEvent.Health(new HealthView("x", FakeEngine.Parse("""{"api": {"port": 5, "name": "ikars"}}"""))));
        await hub.Flush();
        Assert.Equal([new Fact("api.name", "ikars"), new Fact("api.port", "5")], health.Rows.OrderBy(f => f.Label, StringComparer.Ordinal));
        health.Close();
    }
}

public class ShellTests : IDisposable
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private readonly string _folder = Path.Combine(Path.GetTempPath(), "ikars-terminal-tests", Guid.NewGuid().ToString("N"));

    public ShellTests()
    {
        Directory.CreateDirectory(_folder);
        File.WriteAllText(Path.Combine(_folder, "api.json"), """{"port": 5555, "token": "made-up", "api_version": "1.0"}""");
    }

    public void Dispose()
    {
        Directory.Delete(_folder, recursive: true);
        GC.SuppressFinalize(this);
    }

    private sealed class NoStarter : IEngineStarter
    {
        public bool Start() => false;
    }

    [Fact]
    public async Task TheShellConnectsShowsTheOverviewAndBuildsEveryScreen()
    {
        var engine = new FakeEngine();
        var connector = new EngineConnector(_folder, new NoStarter(), _ => engine);
        using var shell = new ShellViewModel(connector, new FrameMeter());
        Assert.Throws<InvalidOperationException>(() => shell.Build(Ref.Overview));   // not connected yet
        await shell.TickAsync();   // nothing to flush yet
        await shell.StartAsync();
        Assert.IsType<OverviewScreen>(shell.Navigator.Current);
        Assert.Equal(5, shell.Menu.Count);
        foreach (var (reference, kind) in new (Ref, Type)[]
        {
            (Ref.Markets, typeof(MarketsScreen)), (Ref.Market("KXA"), typeof(MarketScreen)), (Ref.Ledger, typeof(LedgerScreen)),
            (Ref.Alerts, typeof(AlertsScreen)), (Ref.Health, typeof(HealthScreen)),
        })
        {
            Assert.IsType(kind, shell.Build(reference));
        }

        Assert.Throws<ArgumentException>(() => shell.Build(new Ref("nowhere")));
        await shell.TickAsync();
        Assert.Equal("frame time: measuring", shell.FrameTime);
        Assert.Same(shell.Frames, shell.Frames);
        shell.Problem = "shown";
        Assert.Equal("shown", shell.Problem);
        shell.Stop();
    }

    [Fact]
    public async Task AFailedFlushIsShownNotThrown()
    {
        var engine = new FakeEngine { OpensStream = true };
        using var shell = new ShellViewModel(new EngineConnector(_folder, new NoStarter(), _ => engine), new FrameMeter());
        await shell.StartAsync();
        await engine.Subscribed.Task.WaitAsync(TimeSpan.FromSeconds(10), Ct);
        engine.SubscribeFailure = new InvalidOperationException("subscription refused");
        await shell.Navigator.GoAsync(Ref.Markets);   // new markets wanted
        await shell.TickAsync();
        Assert.Equal("subscription refused", shell.Problem);
        engine.SubscribeFailure = new OperationCanceledException();
        shell.Problem = string.Empty;
        await shell.Navigator.GoAsync(Ref.Market("KXDEMO-26JAN01-A"));
        await shell.TickAsync();
        Assert.Equal(string.Empty, shell.Problem);   // a cancel at stop is not a problem
        shell.Stop();
    }

    [Fact]
    public async Task ScreensReconnectWhenTheEngineRestarted()
    {
        var restarted = new FakeEngine();
        var failing = new FailingEngine();
        var connects = 0;
        var api = new ReconnectingApi(_ =>
        {
            connects++;
            return Task.FromResult<IIkarsApi>(restarted);
        }, failing);
        Assert.Equal("1.0", (await api.HelloAsync(Ct)).ApiVersion);
        Assert.Equal(1, connects);
        await api.HealthAsync(Ct);
        await api.MarketsAsync(null, null, 0, 5, Ct);
        await api.MarketAsync("KXOTHER-1", Ct);
        await api.BookAsync("A", Ct);
        await api.CandlesAsync("A", 1, 1, Ct);
        await api.PortfolioAsync(Ct);
        await api.LedgerStatusAsync(Ct);
        await api.LedgerRecordsAsync(null, 5, cancellationToken: Ct);
        await api.AlertsAsync(null, 5, Ct);
        await api.RecommendationsAsync(Ct);
        await api.GapsAsync(7, Ct);
        await api.SetSubscriptionsAsync("s", new Subscriptions([], []), Ct);
        Assert.NotNull(api.StreamAsync(null, Ct));
        Assert.Equal(1, connects);   // once reconnected, the new engine is used
        Assert.Equal(12, restarted.Calls);   // every read but the subscription change

        var slow = new FailingEngine(() => new EngineUnavailableException("slow", new OperationCanceledException()));
        var patient = new ReconnectingApi(_ =>
        {
            connects++;
            return Task.FromResult<IIkarsApi>(restarted);
        }, slow);
        Assert.True((await Assert.ThrowsAsync<EngineUnavailableException>(() => patient.HealthAsync(Ct))).TimedOut);
        Assert.Equal(1, connects);   // a slow answer is not a restart: not asked twice
    }

    private sealed class FailingEngine(Func<Exception>? failure = null) : IIkarsApi
    {
        private Exception Failure() => failure?.Invoke() ?? new EngineUnavailableException("gone");

        public Task<Hello> HelloAsync(CancellationToken cancellationToken = default) => throw new ApiException(401, "unauthorised", "stale token");

        public Task<HealthView> HealthAsync(CancellationToken cancellationToken = default) => throw Failure();

        public Task<MarketPage> MarketsAsync(string? query, string? category, int offset, int limit, CancellationToken cancellationToken = default) => throw Failure();

        public Task<MarketView> MarketAsync(string ticker, CancellationToken cancellationToken = default) => throw Failure();

        public Task<BookView> BookAsync(string ticker, CancellationToken cancellationToken = default) => throw Failure();

        public Task<CandlePage> CandlesAsync(string ticker, int intervalMinutes, int hours, CancellationToken cancellationToken = default) => throw Failure();

        public Task<PortfolioView> PortfolioAsync(CancellationToken cancellationToken = default) => throw Failure();

        public Task<LedgerStatusView> LedgerStatusAsync(CancellationToken cancellationToken = default) => throw Failure();

        public Task<LedgerPage> LedgerRecordsAsync(long? before, int limit, string? ticker = null, CancellationToken cancellationToken = default) => throw Failure();

        public Task<AlertPage> AlertsAsync(long? after, int limit, CancellationToken cancellationToken = default) => throw Failure();

        public Task<RecommendationPage> RecommendationsAsync(CancellationToken cancellationToken = default) => throw Failure();

        public Task<GapPage> GapsAsync(int days, CancellationToken cancellationToken = default) => throw Failure();

        public Task SetSubscriptionsAsync(string streamId, Subscriptions subscriptions, CancellationToken cancellationToken = default) => throw Failure();

        public IAsyncEnumerable<StreamEvent> StreamAsync(long? afterAlert, CancellationToken cancellationToken = default) => throw Failure();
    }
}
