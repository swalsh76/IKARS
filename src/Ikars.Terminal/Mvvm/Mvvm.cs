using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Input;

namespace Ikars.Terminal.Mvvm;

// From KalTerm3's Mvvm folder: a small base for view models and two commands, so the terminal needs no MVVM toolkit.

/// <summary>The base of every view model: properties that tell the view when they change.</summary>
internal abstract class ObservableObject : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;

    /// <summary>Sets a field and raises the change notification when the value differs.</summary>
    /// <returns><see langword="true"/> when the value changed.</returns>
    protected bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        Raise(name);
        return true;
    }

    protected void Raise([CallerMemberName] string? name = null) => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}

/// <summary>A command that runs a method.</summary>
internal sealed class RelayCommand(Action execute, Func<bool>? canExecute = null) : ICommand
{
    public event EventHandler? CanExecuteChanged;

    public bool CanExecute(object? parameter) => canExecute?.Invoke() ?? true;

    public void Execute(object? parameter) => execute();

    public void RaiseCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}

/// <summary>A command that takes the item it was clicked on (a link).</summary>
internal sealed class RelayCommand<T>(Action<T> execute) : ICommand
{
    public event EventHandler? CanExecuteChanged
    {
        add { }
        remove { }
    }

    public bool CanExecute(object? parameter) => parameter is T;

    public void Execute(object? parameter)
    {
        if (parameter is T value)
        {
            execute(value);
        }
    }
}

/// <summary>
/// A command that runs an async method. It cannot run again while running, and a failure goes to <paramref name="onError"/> instead of being lost
/// (an exception from an async void handler would otherwise end the program).
/// </summary>
internal sealed class AsyncCommand(Func<Task> execute, Action<Exception> onError, Func<bool>? canExecute = null) : ICommand
{
    private bool _running;

    public event EventHandler? CanExecuteChanged;

    public bool CanExecute(object? parameter) => !_running && (canExecute?.Invoke() ?? true);

    public async void Execute(object? parameter) => await RunAsync().ConfigureAwait(true);

    /// <summary>Runs the command and returns when it finishes (tests await this; the view calls <see cref="Execute"/>).</summary>
    public async Task RunAsync()
    {
        if (!CanExecute(null))
        {
            return;
        }

        _running = true;
        RaiseCanExecuteChanged();
        try
        {
            await execute().ConfigureAwait(true);
        }
        catch (OperationCanceledException)
        {
            // cancelled on purpose: not a failure to report
        }
        catch (Exception ex)
        {
            onError(ex);
        }
        finally
        {
            _running = false;
            RaiseCanExecuteChanged();
        }
    }

    public void RaiseCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}
