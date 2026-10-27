using System.Collections.Concurrent;
using Ikars.Client;
using Ikars.Contracts;

namespace Ikars.Terminal.Live;

/// <summary>
/// Between the engine's stream and the screens (PLAN D3: coalesce, repaint at a fixed rate). Stream events arrive on a background thread and only replace the latest
/// value for their key (a market's quote, its book, the portfolio, health), so a burst costs nothing; <see cref="Flush"/>, called on the UI thread about ten times a
/// second, hands each changed value to the screens once. Screens say which markets they show; the hub keeps the stream subscribed to their union.
/// </summary>
internal sealed class UpdateHub
{
    private readonly ConcurrentDictionary<string, QuoteUpdate> _quotes = new();
    private readonly ConcurrentDictionary<string, BookView> _books = new();
    private readonly ConcurrentQueue<AlertView> _alerts = new();
    private readonly Dictionary<object, (string[] Quotes, string[] Books)> _wanted = [];
    private readonly Func<Subscriptions, Task> _subscribe;
    private PortfolioView? _portfolio;
    private HealthView? _health;
    private bool _wantedChanged;

    /// <param name="subscribe">Applies the union of subscriptions to the stream.</param>
    public UpdateHub(Func<Subscriptions, Task> subscribe) => _subscribe = subscribe;

    public event Action<QuoteUpdate>? Quote;

    public event Action<BookView>? Book;

    public event Action<PortfolioView>? Portfolio;

    public event Action<HealthView>? Health;

    public event Action<AlertView>? Alert;

    public event Action<string>? Notice;

    /// <summary>The last connection notice (connected, waiting, why).</summary>
    public string Connection { get; private set; } = "connecting";

    /// <summary>The markets streamed now (the union of what the screens want).</summary>
    public Subscriptions Streaming { get; private set; } = new([], []);

    /// <summary>An event from the stream (any thread): it only replaces the latest value for its key.</summary>
    public void Receive(StreamEvent item)
    {
        switch (item)
        {
            case StreamEvent.Quote quote:
                _quotes[quote.Value.Ticker] = quote.Value;
                break;
            case StreamEvent.Book book:
                _books[book.Value.Ticker] = book.Value;
                break;
            case StreamEvent.Portfolio portfolio:
                Volatile.Write(ref _portfolio, portfolio.Value);
                break;
            case StreamEvent.Health health:
                Volatile.Write(ref _health, health.Value);
                break;
            case StreamEvent.Alert alert:
                _alerts.Enqueue(alert.Value);
                break;
        }
    }

    /// <summary>A line about the connection (any thread).</summary>
    public void ReceiveNotice(string notice) => Connection = notice;

    /// <summary>A screen shows these markets (it replaces what the same screen wanted before).</summary>
    public void Want(object screen, IEnumerable<string> quotes, IEnumerable<string> books)
    {
        string[] quoted = [.. quotes], booked = [.. books];
        if (_wanted.TryGetValue(screen, out var had) && had.Quotes.SequenceEqual(quoted) && had.Books.SequenceEqual(booked))
        {
            return;   // the same markets (the Overview re-sends its positions every second): nothing to send
        }

        _wanted[screen] = (quoted, booked);
        _wantedChanged = true;
    }

    /// <summary>A screen closed: its markets are no longer wanted (unless another screen shows them).</summary>
    public void Release(object screen) => _wantedChanged |= _wanted.Remove(screen);

    /// <summary>
    /// On the UI thread: hands every changed value to the screens once, and updates the stream's subscriptions when the screens changed. Returns whether anything
    /// was handed over (the frame meter times only ticks that change the screen).
    /// </summary>
    public async Task<bool> Flush()
    {
        var handed = false;
        foreach (var ticker in _quotes.Keys)
        {
            if (_quotes.TryRemove(ticker, out var quote))
            {
                Quote?.Invoke(quote);
                handed = true;
            }
        }

        foreach (var ticker in _books.Keys)
        {
            if (_books.TryRemove(ticker, out var book))
            {
                Book?.Invoke(book);
                handed = true;
            }
        }

        if (Interlocked.Exchange(ref _portfolio, null) is { } portfolio)
        {
            Portfolio?.Invoke(portfolio);
            handed = true;
        }

        if (Interlocked.Exchange(ref _health, null) is { } health)
        {
            Health?.Invoke(health);
            handed = true;
        }

        while (_alerts.TryDequeue(out var alert))
        {
            Alert?.Invoke(alert);
            handed = true;
        }

        Notice?.Invoke(Connection);
        if (_wantedChanged)
        {
            _wantedChanged = false;
            var quotes = _wanted.Values.SelectMany(w => w.Quotes).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
            var books = _wanted.Values.SelectMany(w => w.Books).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray();
            Streaming = new Subscriptions(quotes, books);
            try
            {
                await _subscribe(Streaming).ConfigureAwait(true);
            }
            catch
            {
                _wantedChanged = true;   // try again on the next flush
                throw;
            }
        }

        return handed;
    }
}
