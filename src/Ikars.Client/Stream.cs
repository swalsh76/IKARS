using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using Ikars.Contracts;

namespace Ikars.Client;

/// <summary>One event of the engine's stream (PLAN M1.7). An event type this terminal does not know is skipped, so a newer engine can add events.</summary>
public abstract record StreamEvent
{
    /// <summary>The event for an SSE type and its data, or <see langword="null"/> for a type this terminal does not know.</summary>
    /// <param name="type">The SSE event type.</param>
    /// <param name="data">Its JSON.</param>
    /// <returns>The event, or <see langword="null"/>.</returns>
    public static StreamEvent? Parse(string type, string data) => type switch
    {
        "opened" => new Opened(Read<StreamOpened>(data)),
        "quote" => new Quote(Read<QuoteUpdate>(data)),
        "book" => new Book(Read<BookView>(data)),
        "portfolio" => new Portfolio(Read<PortfolioView>(data)),
        "alert" => new Alert(Read<AlertView>(data)),
        "health" => new Health(Read<HealthView>(data)),
        "error" => new Refused(Read<ApiError>(data)),
        _ => null,
    };

    private static T Read<T>(string data) => JsonSerializer.Deserialize<T>(data, ContractJson.Options) ?? throw new JsonException("an empty stream event");

    /// <summary>The stream is open: its id, for subscriptions.</summary>
    public sealed record Opened(StreamOpened Value) : StreamEvent;

    /// <summary>A market's quote changed.</summary>
    public sealed record Quote(QuoteUpdate Value) : StreamEvent;

    /// <summary>A market's book changed.</summary>
    public sealed record Book(BookView Value) : StreamEvent;

    /// <summary>The portfolio changed.</summary>
    public sealed record Portfolio(PortfolioView Value) : StreamEvent;

    /// <summary>A new alert-log line.</summary>
    public sealed record Alert(AlertView Value) : StreamEvent;

    /// <summary>The engine's health (every few seconds).</summary>
    public sealed record Health(HealthView Value) : StreamEvent;

    /// <summary>The stream could not open (too many at once); it ends.</summary>
    public sealed record Refused(ApiError Value) : StreamEvent;
}

/// <summary>Reads Server-Sent Events (from KalTerm3): lines of <c>field: value</c>, a blank line ending each event, comment lines ignored.</summary>
internal static class ServerSentEvents
{
    /// <summary>The events of a stream, as (type, id, data), until it ends.</summary>
    public static async IAsyncEnumerable<(string Type, string? Id, string Data)> ReadAsync(TextReader reader, [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        var type = "message";
        string? id = null;
        var data = new StringBuilder();
        var any = false;
        while (await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false) is { } line)
        {
            if (line.Length == 0)
            {
                if (any)
                {
                    yield return (type, id, data.ToString());
                }

                type = "message";
                id = null;
                data.Clear();
                any = false;
                continue;
            }

            if (line[0] == ':')
            {
                continue;
            }

            var colon = line.IndexOf(':', StringComparison.Ordinal);
            var field = colon < 0 ? line : line[..colon];
            var value = colon < 0 ? string.Empty : line[(colon + 1)..].TrimStart(' ');
            switch (field)
            {
                case "event":
                    type = value;
                    any = true;
                    break;
                case "id":
                    id = value;
                    any = true;
                    break;
                case "data":
                    if (data.Length > 0)
                    {
                        data.Append('\n');
                    }

                    data.Append(value);
                    any = true;
                    break;
            }
        }
    }
}

/// <summary>
/// Keeps the stream going (from KalTerm3's watchers): it connects (reading <c>api.json</c> each time, since a restarted engine has a new port and token), opens the
/// stream from the last alert line it saw, applies the wanted subscriptions, and hands every event over. When the connection drops or the engine cannot be reached it
/// waits (1 s, doubling to 30 s, starting over once anything arrives) and tries again. An engine of another major version ends it: waiting will not cure that.
/// </summary>
/// <param name="connect">Connects to the engine as it is now (and checks the handshake).</param>
/// <param name="delay">How a wait is made (tests pass one that returns at once).</param>
public sealed class StreamWatcher(Func<CancellationToken, Task<IIkarsApi>> connect, Func<TimeSpan, CancellationToken, Task>? delay = null)
{
    /// <summary>The first wait after a failure.</summary>
    public static readonly TimeSpan FirstWait = TimeSpan.FromSeconds(1);

    /// <summary>The longest wait.</summary>
    public static readonly TimeSpan LongestWait = TimeSpan.FromSeconds(30);

    /// <summary>A stream silent this long is dead (a hung engine, a half-open socket): the engine sends health every 5 s.</summary>
    public TimeSpan IdleTimeout { get; init; } = TimeSpan.FromSeconds(20);

    private readonly Func<TimeSpan, CancellationToken, Task> _delay = delay ?? Task.Delay;
    private readonly object _gate = new();
    private Subscriptions _wanted = new([], []);
    private (IIkarsApi Api, string Id)? _open;

    /// <summary>The last alert line seen: a reconnect resumes after it.</summary>
    public long? AlertLine { get; private set; }

    /// <summary>Sets the markets whose quotes and books the stream should carry (applied now if open, and on every reconnect).</summary>
    /// <param name="wanted">The markets.</param>
    /// <param name="cancellationToken">Cancels the request.</param>
    /// <returns>When applied (or remembered).</returns>
    public async Task SetWantedAsync(Subscriptions wanted, CancellationToken cancellationToken = default)
    {
        (IIkarsApi Api, string Id)? open;
        lock (_gate)
        {
            _wanted = wanted;
            open = _open;
        }

        if (open is { } stream)
        {
            try
            {
                await stream.Api.SetSubscriptionsAsync(stream.Id, wanted, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is ApiException or EngineUnavailableException)
            {
                // the stream is going away: the reconnect applies them
            }
        }
    }

    /// <summary>Runs until cancelled or the engine is incompatible.</summary>
    /// <param name="onEvent">Each event.</param>
    /// <param name="onNotice">A line about the connection for the person (connected, waiting, why).</param>
    /// <param name="cancellationToken">Stops it.</param>
    /// <returns>When it stops.</returns>
    public async Task RunAsync(Action<StreamEvent> onEvent, Action<string> onNotice, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(onEvent);
        ArgumentNullException.ThrowIfNull(onNotice);
        var wait = FirstWait;
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                var api = await connect(cancellationToken).ConfigureAwait(false);
                using var idle = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                idle.CancelAfter(IdleTimeout);
                await foreach (var item in api.StreamAsync(AlertLine, idle.Token).ConfigureAwait(false))
                {
                    idle.CancelAfter(IdleTimeout);
                    if (item is not StreamEvent.Refused)
                    {
                        wait = FirstWait;   // a refusal (too many streams) keeps backing off
                    }
                    switch (item)
                    {
                        case StreamEvent.Opened opened:
                            Subscriptions wanted;
                            lock (_gate)
                            {
                                _open = (api, opened.Value.StreamId);
                                wanted = _wanted;
                            }

                            await api.SetSubscriptionsAsync(opened.Value.StreamId, wanted, cancellationToken).ConfigureAwait(false);
                            onNotice("connected");
                            break;
                        case StreamEvent.Alert alert:
                            AlertLine = alert.Value.Line;
                            break;
                        case StreamEvent.Refused refused:
                            onNotice("the engine refused the stream: " + refused.Value.Detail);
                            break;
                    }

                    onEvent(item);
                }

                onNotice("the stream ended; reconnecting");
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (OperationCanceledException)
            {
                onNotice($"the stream went quiet; reconnecting in {wait.TotalSeconds:0} s");
            }
            catch (IncompatibleEngineException ex)
            {
                onNotice(ex.Message);
                return;
            }
            catch (Exception ex) when (ex is EngineUnavailableException or ApiException or IOException or HttpRequestException or JsonException)
            {
                onNotice($"{Describe(ex)}; trying again in {wait.TotalSeconds:0} s");
            }
            finally
            {
                lock (_gate)
                {
                    _open = null;
                }
            }

            try
            {
                await _delay(wait, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                return;
            }

            wait = TimeSpan.FromSeconds(Math.Min(LongestWait.TotalSeconds, wait.TotalSeconds * 2));
        }
    }

    private static string Describe(Exception ex) => ex switch
    {
        EngineUnavailableException => "the engine is not answering",
        ApiException api => $"the engine answered {api.Status} ({api.Code})",
        JsonException => "the engine sent something unreadable",
        _ => "the connection dropped",
    };
}
