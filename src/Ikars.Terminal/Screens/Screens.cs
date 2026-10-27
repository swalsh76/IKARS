using System.Collections.ObjectModel;
using System.Text.Json;
using Ikars.Client;
using Ikars.Contracts;
using Ikars.Terminal.Live;
using Ikars.Terminal.Mvvm;
using Ikars.Terminal.Navigation;

namespace Ikars.Terminal.Screens;

// The screens of M1.8a, per the approved mock-ups (docs/design/mockups). Each is built from a reference, loads its snapshot when it opens, takes live updates from
// the hub while open, and lets go when it closes. Money and counts stay integers until they become text (Formatting).

/// <summary>What every screen needs: the engine, the live hub, navigation, and where to put an error for the person.</summary>
internal sealed record ScreenContext(IIkarsApi Api, UpdateHub Hub, Navigator Navigator, Selection Selection, FrameMeter Frames, Action<string> Problem);

/// <summary>The item selected across panels (PLAN R9: selection stays in sync); panels highlight it.</summary>
internal sealed class Selection : ObservableObject
{
    private Ref? _current;

    public Ref? Current
    {
        get => _current;
        set => Set(ref _current, value);
    }
}

/// <summary>A row that is also a link.</summary>
internal sealed record LinkRow(Ref Target, string Primary, string Secondary, string Value = "");

/// <summary>An Overview panel whose data arrives in a later slice (owner, 2026-10-27: drawn now, in place).</summary>
internal sealed record PlannedPanel(string Title, string ArrivesIn);

/// <summary>A label and a value.</summary>
internal sealed record Fact(string Label, string Value, bool Good = true);

/// <summary>
/// Updates a shown list in place (D20 J): only rows whose content changed are replaced, so a list refreshed every second or every tick does not make WPF
/// lay out every row again. A cleared and refilled list cost the console a missed refresh at idle (M1.8a).
/// </summary>
internal static class Rows
{
    public static void Sync<T>(ObservableCollection<T> shown, IEnumerable<T> wanted)
    {
        var at = 0;
        foreach (var row in wanted)
        {
            if (at == shown.Count)
            {
                shown.Add(row);
            }
            else if (!EqualityComparer<T>.Default.Equals(shown[at], row))
            {
                shown[at] = row;
            }

            at++;
        }

        while (shown.Count > at)
        {
            shown.RemoveAt(shown.Count - 1);
        }
    }
}

/// <summary>The Overview: portfolio and exposure, alerts and health are live now; the other panels are drawn with when they arrive.</summary>
internal sealed class OverviewScreen(ScreenContext ctx) : Screen(Ref.Overview)
{
    private string _cash = "—";
    private string _openCost = "—";
    private string _unrealised = "—";

    public static IReadOnlyList<PlannedPanel> Planned { get; } =
    [
        new("Top recommendations", "M1.12"),
        new("Live movers (5 min)", "M1.12"),
        new("Latest source items", "M2"),
        new("Journal scorecard (30 days)", "M1.12"),
        new("Perp funding", "M3"),
        new("Book signals", "M3"),
    ];

    public string Cash { get => _cash; private set => Set(ref _cash, value); }

    public string OpenCost { get => _openCost; private set => Set(ref _openCost, value); }

    public string Unrealised { get => _unrealised; private set => Set(ref _unrealised, value); }

    public ObservableCollection<LinkRow> Positions { get; } = [];

    public ObservableCollection<Fact> Exposure { get; } = [];

    public ObservableCollection<AlertView> Alerts { get; } = [];

    public ObservableCollection<Fact> HealthStrip { get; } = [];

    public RelayCommand<Ref> Open => ctx.Navigator.GoCommand;

    public override async Task OpenAsync(CancellationToken cancellationToken)
    {
        ctx.Hub.Portfolio += ShowPortfolio;
        ctx.Hub.Alert += AddAlert;
        ctx.Hub.Health += ShowHealth;
        ShowPortfolio(await ctx.Api.PortfolioAsync(cancellationToken).ConfigureAwait(true));
        foreach (var alert in (await ctx.Api.AlertsAsync(null, AlertLists.OnOverview, cancellationToken).ConfigureAwait(true)).Items)
        {
            AddAlert(alert);
        }

        ShowHealth(await ctx.Api.HealthAsync(cancellationToken).ConfigureAwait(true));
    }

    public override void Close()
    {
        ctx.Hub.Portfolio -= ShowPortfolio;
        ctx.Hub.Alert -= AddAlert;
        ctx.Hub.Health -= ShowHealth;
        ctx.Hub.Release(this);
    }

    internal void ShowPortfolio(PortfolioView portfolio)
    {
        Cash = Formatting.Dollars(portfolio.CashE6);
        OpenCost = Formatting.Dollars(portfolio.CostE6);
        Unrealised = portfolio.Positions.Count == 0 ? "no open positions" : portfolio.UnrealisedE6 is null ? "not all priced" : Formatting.Signed(portfolio.UnrealisedE6);
        Rows.Sync(Positions, portfolio.Positions.Select(p => new LinkRow(
            Ref.Market(p.Ticker), p.Title.Length > 0 ? p.Title : p.Ticker, $"{p.Side.ToUpperInvariant()} {Formatting.Contracts(p.CountE2)} · cost {Formatting.Dollars(p.CostE6)}", Formatting.Signed(p.UnrealisedE6))));
        Rows.Sync(Exposure, portfolio.ExposureByCategory.Select(e => new Fact(e.Key, Formatting.Dollars(e.Value))));

        ctx.Hub.Want(this, portfolio.Positions.Select(p => p.Ticker), []);
    }

    internal void AddAlert(AlertView alert) => AlertLists.Add(Alerts, alert, AlertLists.OnOverview);

    internal void ShowHealth(HealthView health)
    {
        var parts = health.Parts;
        var connected = Read(parts, "kalshi", "feed", "connected");
        var exact = Read(parts, "ledger", "reconciled_exact");
        var backupHours = Read(parts, "backup", "hours_since");
        Rows.Sync(HealthStrip,
        [
            new Fact("Feed", connected == "True" ? "connected" : "not connected", connected == "True"),
            new Fact("Ledger", exact == "True" ? "reconciles exactly" : exact is null ? "checking" : "differs from Kalshi", exact != "False"),
            new Fact("Backup", backupHours is null ? "none yet" : $"{backupHours} h ago", backupHours is not null && int.TryParse(backupHours, out var h) && h <= 36),
            new Fact("Open gaps", Read(parts, "kalshi", "open_gaps") ?? "—", Read(parts, "kalshi", "open_gaps") is "0" or null),
            new Fact("Update to screen", ctx.Frames.Text, ctx.Frames.Count == 0 || ctx.Frames.WithinTarget),
        ]);
    }

    /// <summary>A value deep in the health parts as text, or <see langword="null"/>.</summary>
    internal static string? Read(IReadOnlyDictionary<string, JsonElement> parts, params string[] path)
    {
        if (path.Length == 0 || !parts.TryGetValue(path[0], out var element))
        {
            return null;
        }

        foreach (var key in path.Skip(1))
        {
            if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(key, out element))
            {
                return null;
            }
        }

        return element.ValueKind switch
        {
            JsonValueKind.True => "True",
            JsonValueKind.False => "False",
            JsonValueKind.Null or JsonValueKind.Undefined => null,
            JsonValueKind.String => element.GetString(),
            _ => element.GetRawText(),
        };
    }
}

/// <summary>A market row in a list, with its live quote.</summary>
internal sealed class MarketRow(MarketView market) : ObservableObject
{
    private string _bid = Formatting.Cents(market.YesBidE6);
    private string _ask = Formatting.Cents(market.YesAskE6);
    private bool _selected;

    public Ref Target { get; } = Ref.Market(market.Ticker);

    public string Ticker => market.Ticker;

    public string Title => market.Title;

    public string Category => market.Category;

    public bool Watched => market.Watched;

    public string Bid { get => _bid; private set => Set(ref _bid, value); }

    public string Ask { get => _ask; private set => Set(ref _ask, value); }

    public bool Selected { get => _selected; set => Set(ref _selected, value); }

    public void Show(QuoteUpdate quote)
    {
        Bid = Formatting.Cents(quote.YesBidE6);
        Ask = Formatting.Cents(quote.YesAskE6);
    }
}

/// <summary>Markets: search and category over the engine's index, by volume; the rows on screen stream their quotes.</summary>
internal sealed class MarketsScreen(ScreenContext ctx) : Screen(Ref.Markets)
{
    public const int PageSize = 50;
    private string _query = string.Empty;
    private string _category = string.Empty;
    private int _offset;
    private long _total;
    private bool _shown;

    public string Query { get => _query; set => Set(ref _query, value); }

    public string Category { get => _category; set => Set(ref _category, value); }

    public string Summary => _total == 0 ? "no markets" : $"{_offset + 1}–{_offset + Rows.Count} of {_total:N0}";

    public ObservableCollection<MarketRow> Rows { get; } = [];

    public RelayCommand<Ref> Open => ctx.Navigator.GoCommand;

    public AsyncCommand Search => new(() => LoadAsync(0, CancellationToken.None), ex => ctx.Problem(ex.Message));

    public AsyncCommand Next => new(() => LoadAsync(_offset + PageSize, CancellationToken.None), ex => ctx.Problem(ex.Message), () => _offset + PageSize < _total);

    public AsyncCommand Previous => new(() => LoadAsync(Math.Max(0, _offset - PageSize), CancellationToken.None), ex => ctx.Problem(ex.Message), () => _offset > 0);

    public override Task OpenAsync(CancellationToken cancellationToken)
    {
        _shown = true;
        ctx.Hub.Quote += ShowQuote;
        ctx.Selection.PropertyChanged += Highlight;
        return LoadAsync(0, cancellationToken);
    }

    public override void Close()
    {
        _shown = false;
        ctx.Hub.Quote -= ShowQuote;
        ctx.Selection.PropertyChanged -= Highlight;
        ctx.Hub.Release(this);
    }

    internal async Task LoadAsync(int offset, CancellationToken cancellationToken)
    {
        var page = await ctx.Api.MarketsAsync(Query, string.IsNullOrWhiteSpace(Category) ? null : Category, offset, PageSize, cancellationToken).ConfigureAwait(true);
        if (!_shown)
        {
            return;   // a page that arrived after the screen closed must not subscribe again
        }

        _offset = (int)page.Offset;
        _total = page.Total;
        Rows.Clear();
        foreach (var market in page.Items)
        {
            Rows.Add(new MarketRow(market) { Selected = ctx.Selection.Current == Ref.Market(market.Ticker) });
        }

        ctx.Hub.Want(this, Rows.Select(r => r.Ticker), []);
        Raise(nameof(Summary));
        Raise(nameof(Next));
        Raise(nameof(Previous));
    }

    private void ShowQuote(QuoteUpdate quote)
    {
        foreach (var row in Rows)
        {
            if (row.Ticker == quote.Ticker)
            {
                row.Show(quote);
            }
        }
    }

    private void Highlight(object? sender, System.ComponentModel.PropertyChangedEventArgs e)
    {
        foreach (var row in Rows)
        {
            row.Selected = ctx.Selection.Current == row.Target;
        }
    }
}

/// <summary>One price level shown in a book.</summary>
internal sealed record BookLevel(string Price, string Contracts);

/// <summary>
/// A market (the drill-down, per the Market mock-up): its live quote, the YES price chart for today, the order book (both sides are bids), and the linked-items
/// panel (same-event markets and the owner's ledger records now; source items, recommendations and perps join in their slices).
/// </summary>
internal sealed class MarketScreen(ScreenContext ctx, string ticker) : Screen(Ref.Market(ticker))
{
    public const int BookDepth = 10;
    private MarketView? _market;
    private string _bid = "—";
    private string _ask = "—";
    private string _last = "—";
    private string _bookNote = "loading";
    private int _chartVersion;

    public override string Title => _market?.Title is { Length: > 0 } title ? title : ticker;

    public string Ticker => ticker;

    public string Bid { get => _bid; private set => Set(ref _bid, value); }

    public string Ask { get => _ask; private set => Set(ref _ask, value); }

    public string Last { get => _last; private set => Set(ref _last, value); }

    public string Details => _market is null ? string.Empty : $"{_market.Category} · {_market.EventTicker} · closes {(_market.CloseTimeUtc is { } close ? Formatting.LocalTime(close) : "—")}";

    public string BookNote { get => _bookNote; private set => Set(ref _bookNote, value); }

    public ObservableCollection<BookLevel> YesBids { get; } = [];

    public ObservableCollection<BookLevel> NoBids { get; } = [];

    /// <summary>The chart's points: period ends and closing traded prices in cents (the view draws them; it redraws when <see cref="ChartVersion"/> moves).</summary>
    public IReadOnlyList<(DateTime End, double Cents)> ChartPoints { get; private set; } = [];

    public int ChartVersion { get => _chartVersion; private set => Set(ref _chartVersion, value); }

    /// <summary>Why there is no chart, or empty when there is one.</summary>
    public string ChartNote { get; private set; } = string.Empty;

    public ObservableCollection<LinkRow> SameEvent { get; } = [];

    public ObservableCollection<LinkRow> LedgerRecords { get; } = [];

    public RelayCommand<Ref> Open => ctx.Navigator.GoCommand;

    public override async Task OpenAsync(CancellationToken cancellationToken)
    {
        ctx.Selection.Current = Reference;
        ctx.Hub.Quote += ShowQuote;
        ctx.Hub.Book += ShowBook;
        _market = await ctx.Api.MarketAsync(ticker, cancellationToken).ConfigureAwait(true);
        Raise(nameof(Title));
        Raise(nameof(Details));
        ShowQuote(new QuoteUpdate(ticker, _market.YesBidE6, _market.YesAskE6, _market.LastPriceE6, null));
        ctx.Hub.Want(this, [ticker], _market.Watched ? [ticker] : []);
        if (_market.Watched)
        {
            ShowBook(await ctx.Api.BookAsync(ticker, cancellationToken).ConfigureAwait(true));
        }
        else
        {
            BookNote = "the engine keeps books for its watched markets only";
        }

        await LoadChartAsync(cancellationToken).ConfigureAwait(true);
        await LoadLinkedAsync(cancellationToken).ConfigureAwait(true);
    }

    public override void Close()
    {
        ctx.Hub.Quote -= ShowQuote;
        ctx.Hub.Book -= ShowBook;
        ctx.Hub.Release(this);
    }

    internal void ShowQuote(QuoteUpdate quote)
    {
        if (quote.Ticker != ticker)
        {
            return;
        }

        Bid = Formatting.Cents(quote.YesBidE6);
        Ask = Formatting.Cents(quote.YesAskE6);
        Last = Formatting.Cents(quote.LastPriceE6);
    }

    internal void ShowBook(BookView book)
    {
        if (book.Ticker != ticker)
        {
            return;
        }

        Replace(YesBids, book.Yes);
        Replace(NoBids, book.No);
        BookNote = book.Ready ? string.Empty : "rebuilding (waiting for a snapshot)";
    }

    private static void Replace(ObservableCollection<BookLevel> rows, IReadOnlyList<Level> levels) =>
        Rows.Sync(rows, levels.Take(BookDepth).Select(level => new BookLevel(Formatting.Cents(level.PriceE6), Formatting.Contracts(level.CountE2))));

    private async Task LoadChartAsync(CancellationToken cancellationToken)
    {
        try
        {
            var page = await ctx.Api.CandlesAsync(ticker, 1, 24, cancellationToken).ConfigureAwait(true);
            ChartPoints = [.. page.Items
                .Where(c => c.CloseE6 is not null || c.BidCloseE6 is not null)
                .Select(c => (DateTimeOffset.Parse(c.EndUtc, System.Globalization.CultureInfo.InvariantCulture).UtcDateTime, (double)((c.CloseE6 ?? c.BidCloseE6)!.Value / 10_000m)))];
            ChartNote = ChartPoints.Count > 0 ? string.Empty : "no trades today";
        }
        catch (ApiException ex) when (ex.Status is 404 or 503)
        {
            ChartPoints = [];
            ChartNote = ex.Status == 404 ? "no price history for this market" : "price history unavailable now (Kalshi did not answer)";
        }

        ChartVersion++;
    }

    private async Task LoadLinkedAsync(CancellationToken cancellationToken)
    {
        if (_market is null)
        {
            return;
        }

        var siblings = await ctx.Api.MarketsAsync(_market.EventTicker, null, 0, 50, cancellationToken).ConfigureAwait(true);
        SameEvent.Clear();
        foreach (var other in siblings.Items.Where(m => m.EventTicker == _market.EventTicker && m.Ticker != ticker))
        {
            SameEvent.Add(new LinkRow(Ref.Market(other.Ticker), other.Title, other.Ticker, Formatting.Cents(other.YesBidE6)));
        }

        var records = await ctx.Api.LedgerRecordsAsync(null, 20, ticker, cancellationToken).ConfigureAwait(true);
        LedgerRecords.Clear();
        foreach (var record in records.Items)
        {
            LedgerRecords.Add(new LinkRow(Ref.Ledger, $"{record.Kind} · {Formatting.LocalTime(record.TimeUtc)}", record.Note ?? string.Empty, Formatting.Signed(record.CashE6)));
        }
    }
}

/// <summary>The ledger (the owner's own records; local only): its status and pages, newest first.</summary>
internal sealed class LedgerScreen(ScreenContext ctx) : Screen(Ref.Ledger)
{
    public const int PageSize = 100;
    private string _status = "loading";
    private long? _older;

    public string Status { get => _status; private set => Set(ref _status, value); }

    public ObservableCollection<LinkRow> Records { get; } = [];

    public AsyncCommand Newest => new(() => LoadAsync(null, CancellationToken.None), ex => ctx.Problem(ex.Message));

    public AsyncCommand Older => new(() => LoadAsync(_older, CancellationToken.None), ex => ctx.Problem(ex.Message), () => _older is not null);

    public RelayCommand<Ref> Open => ctx.Navigator.GoCommand;

    public override async Task OpenAsync(CancellationToken cancellationToken)
    {
        var status = await ctx.Api.LedgerStatusAsync(cancellationToken).ConfigureAwait(true);
        var chain = status.Verified switch { true => "chain verified", false => "CHAIN BROKEN", null => "checking the chain" };
        var reconciled = status.ReconciledExact switch { true => "reconciles exactly with Kalshi", false => "differs from Kalshi", null => "not reconciled yet" };
        Status = $"{status.HeadSeq:N0} records · {chain} · {reconciled} · tax zone {status.TaxZone}";
        await LoadAsync(null, cancellationToken).ConfigureAwait(true);
    }

    internal async Task LoadAsync(long? before, CancellationToken cancellationToken)
    {
        var page = await ctx.Api.LedgerRecordsAsync(before, PageSize, null, cancellationToken).ConfigureAwait(true);
        _older = page.Before;
        Records.Clear();
        foreach (var record in page.Items)
        {
            var target = record.Ticker is { } market ? Ref.Market(market) : Ref.Ledger;
            Records.Add(new LinkRow(target, $"#{record.Seq} {record.Kind}", $"{Formatting.LocalTime(record.TimeUtc)} · {record.Ticker ?? record.Note ?? string.Empty}", Formatting.Signed(record.CashE6)));
        }

        Raise(nameof(Older));
    }
}

/// <summary>Alerts by priority (the Alerts mock-up), live.</summary>
internal sealed class AlertsScreen(ScreenContext ctx) : Screen(Ref.Alerts)
{
    public ObservableCollection<AlertView> Urgent { get; } = [];

    public ObservableCollection<AlertView> High { get; } = [];

    public ObservableCollection<AlertView> Other { get; } = [];

    public override async Task OpenAsync(CancellationToken cancellationToken)
    {
        ctx.Hub.Alert += Add;
        foreach (var alert in (await ctx.Api.AlertsAsync(null, 200, cancellationToken).ConfigureAwait(true)).Items)
        {
            Add(alert);
        }
    }

    public override void Close() => ctx.Hub.Alert -= Add;

    internal void Add(AlertView alert) =>
        AlertLists.Add(alert.Priority switch { "urgent" => Urgent, "high" => High, _ => Other }, alert, AlertLists.PerPriority);
}

/// <summary>
/// Alert lists newest first by log line, each alert once: a screen listens before it reads the log's snapshot (so nothing falls between), and an alert can then
/// arrive both ways.
/// </summary>
internal static class AlertLists
{
    public const int OnOverview = 8;
    public const int PerPriority = 200;

    public static void Add(ObservableCollection<AlertView> list, AlertView alert, int most)
    {
        var at = 0;
        while (at < list.Count && list[at].Line > alert.Line)
        {
            at++;
        }

        if (at < list.Count && list[at].Line == alert.Line)
        {
            return;
        }

        list.Insert(at, alert);
        while (list.Count > most)
        {
            list.RemoveAt(list.Count - 1);
        }
    }
}

/// <summary>Health: the engine's parts as rows, the gap log for the last week, and the terminal's own frame time.</summary>
internal sealed class HealthScreen(ScreenContext ctx) : Screen(Ref.Health)
{
    public ObservableCollection<Fact> Rows { get; } = [];

    public ObservableCollection<GapView> Gaps { get; } = [];

    public string FrameTime => $"{ctx.Frames.Text} (target p99 ≤ {FrameMeter.TargetP99Ms:0} ms)";

    public override async Task OpenAsync(CancellationToken cancellationToken)
    {
        ctx.Hub.Health += Show;
        Show(await ctx.Api.HealthAsync(cancellationToken).ConfigureAwait(true));
        foreach (var gap in (await ctx.Api.GapsAsync(7, cancellationToken).ConfigureAwait(true)).Items)
        {
            Gaps.Add(gap);
        }
    }

    public override void Close() => ctx.Hub.Health -= Show;

    internal void Show(HealthView health)
    {
        var facts = new List<Fact>();
        foreach (var (part, element) in health.Parts.OrderBy(p => p.Key, StringComparer.Ordinal))
        {
            Flatten(facts, part, element);
        }

        Screens.Rows.Sync(Rows, facts);
        Raise(nameof(FrameTime));
    }

    private static void Flatten(List<Fact> facts, string path, JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in element.EnumerateObject())
            {
                Flatten(facts, $"{path}.{property.Name}", property.Value);
            }

            return;
        }

        facts.Add(new Fact(path, element.ValueKind == JsonValueKind.String ? element.GetString() ?? string.Empty : element.GetRawText()));
    }
}
