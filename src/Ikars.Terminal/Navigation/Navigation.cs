using System.Collections.ObjectModel;
using Ikars.Terminal.Mvvm;

namespace Ikars.Terminal.Navigation;

/// <summary>
/// One typed reference to anything the terminal can show (PLAN R9): a kind and an id. A link is a reference; every screen is built from one, so a new kind of item
/// adds a screen, never a new way of navigating.
/// </summary>
/// <param name="Kind">What it is: overview, markets, market, ledger, alerts, health.</param>
/// <param name="Id">Which one (a ticker for a market); empty for a screen with no id.</param>
internal sealed record Ref(string Kind, string Id = "")
{
    public static readonly Ref Overview = new("overview");
    public static readonly Ref Markets = new("markets");
    public static readonly Ref Ledger = new("ledger");
    public static readonly Ref Alerts = new("alerts");
    public static readonly Ref Health = new("health");

    /// <summary>A market, by ticker.</summary>
    public static Ref Market(string ticker) => new("market", ticker);

    /// <summary>The name in the breadcrumb trail.</summary>
    public string Label => Kind switch
    {
        "overview" => "Overview",
        "markets" => "Markets",
        "ledger" => "Ledger",
        "alerts" => "Alerts",
        "health" => "Health",
        _ => Id,
    };

    /// <summary>Where this sits in the drill-down: a market under Markets, every other screen at the top.</summary>
    public IReadOnlyList<Ref> Trail => Kind == "market" ? [Overview, Markets, this] : Kind == "overview" ? [this] : [Overview, this];
}

/// <summary>A screen: built from a reference, told when it opens and closes (it subscribes to what it shows, and lets go).</summary>
internal abstract class Screen(Ref reference) : ObservableObject
{
    /// <summary>What it shows.</summary>
    public Ref Reference { get; } = reference;

    /// <summary>Its title.</summary>
    public virtual string Title => Reference.Label;

    /// <summary>It is shown: load and subscribe.</summary>
    public virtual Task OpenAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    /// <summary>It is hidden: let go of subscriptions.</summary>
    public virtual void Close()
    {
    }
}

/// <summary>
/// The one navigation service (PLAN R9): drill down one level at a time, a breadcrumb trail, back and forward. Every screen comes from a reference through the factory,
/// and is opened and closed as it comes and goes.
/// </summary>
internal sealed class Navigator : ObservableObject, IDisposable
{
    private readonly Func<Ref, Screen> _screenFor;
    private readonly Action<Exception> _onError;
    private readonly Stack<Ref> _back = new();
    private readonly Stack<Ref> _forward = new();
    private Screen? _current;
    private CancellationTokenSource? _opening;

    public Navigator(Func<Ref, Screen> screenFor, Action<Exception> onError)
    {
        _screenFor = screenFor;
        _onError = onError;
        BackCommand = new RelayCommand(() => _ = GoBackAsync(), () => _back.Count > 0);
        ForwardCommand = new RelayCommand(() => _ = GoForwardAsync(), () => _forward.Count > 0);
        GoCommand = new RelayCommand<Ref>(reference => _ = GoAsync(reference));
    }

    /// <summary>The screen shown.</summary>
    public Screen? Current
    {
        get => _current;
        private set => Set(ref _current, value);
    }

    /// <summary>The trail to the current screen, each a link.</summary>
    public ObservableCollection<Ref> Breadcrumbs { get; } = [];

    public RelayCommand BackCommand { get; }

    public RelayCommand ForwardCommand { get; }

    /// <summary>Opens a reference (a link, a breadcrumb, a menu item).</summary>
    public RelayCommand<Ref> GoCommand { get; }

    public bool CanGoBack => _back.Count > 0;

    public bool CanGoForward => _forward.Count > 0;

    /// <summary>Opens <paramref name="reference"/>; the current screen goes on the back stack and forward history is dropped.</summary>
    public Task GoAsync(Ref reference)
    {
        if (Current?.Reference == reference)
        {
            return Task.CompletedTask;
        }

        if (Current is { } shown)
        {
            _back.Push(shown.Reference);
        }

        _forward.Clear();
        return ShowAsync(reference);
    }

    public Task GoBackAsync()
    {
        if (_back.Count == 0)
        {
            return Task.CompletedTask;
        }

        if (Current is { } shown)
        {
            _forward.Push(shown.Reference);
        }

        return ShowAsync(_back.Pop());
    }

    public Task GoForwardAsync()
    {
        if (_forward.Count == 0)
        {
            return Task.CompletedTask;
        }

        if (Current is { } shown)
        {
            _back.Push(shown.Reference);
        }

        return ShowAsync(_forward.Pop());
    }

    public void Dispose()
    {
        _opening?.Cancel();
        _opening?.Dispose();
    }

    private async Task ShowAsync(Ref target)
    {
        // A screen left while it is still loading has its load cancelled, so nothing it asks for outlives it.
        _opening?.Cancel();
        _opening?.Dispose();
        _opening = new CancellationTokenSource();
        var token = _opening.Token;
        Current?.Close();
        var screen = _screenFor(target);
        Current = screen;
        Breadcrumbs.Clear();
        foreach (var step in target.Trail)
        {
            Breadcrumbs.Add(step);
        }

        BackCommand.RaiseCanExecuteChanged();
        ForwardCommand.RaiseCanExecuteChanged();
        Raise(nameof(CanGoBack));
        Raise(nameof(CanGoForward));
        try
        {
            await screen.OpenAsync(token).ConfigureAwait(true);
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            _onError(ex);
        }

        if (!ReferenceEquals(Current, screen))
        {
            screen.Close();   // it was left mid-load: let go of what the load subscribed to
        }
    }
}
