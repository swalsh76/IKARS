using System.Diagnostics;
using Microsoft.Win32;

namespace Ikars.Client;

/// <summary>Starts the engine the way <c>install-autostart</c> registered it (the HKCU Run value <c>IKARS Engine</c>), so the terminal and sign-in start the same thing.</summary>
public interface IEngineStarter
{
    /// <summary>Starts the engine; <see langword="false"/> when no start command is registered.</summary>
    bool Start();
}

/// <summary><see cref="IEngineStarter"/> from the registered Run value.</summary>
public sealed class RegisteredEngineStarter : IEngineStarter
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string RunValue = "IKARS Engine";

    /// <inheritdoc/>
    public bool Start()
    {
        using var key = Registry.CurrentUser.OpenSubKey(RunKey);
        if (key?.GetValue(RunValue) is not string command || CommandLine.Split(command) is not [var program, .. var arguments])
        {
            return false;
        }

        var start = new ProcessStartInfo(program) { UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile) };
        foreach (var argument in arguments)
        {
            start.ArgumentList.Add(argument);
        }

        using var process = Process.Start(start);
        return process is not null;
    }
}

/// <summary>Splits a command line the way the Run value is written: words, with double quotes around a word that holds spaces.</summary>
public static class CommandLine
{
    /// <summary>The words of <paramref name="command"/>.</summary>
    /// <param name="command">The command line.</param>
    /// <returns>Its words, quotes removed.</returns>
    public static string[] Split(string command)
    {
        ArgumentNullException.ThrowIfNull(command);
        var words = new List<string>();
        var word = new System.Text.StringBuilder();
        var quoted = false;
        var started = false;
        foreach (var ch in command)
        {
            if (ch == '"')
            {
                quoted = !quoted;
                started = true;
            }
            else if (char.IsWhiteSpace(ch) && !quoted)
            {
                if (started)
                {
                    words.Add(word.ToString());
                    word.Clear();
                    started = false;
                }
            }
            else
            {
                word.Append(ch);
                started = true;
            }
        }

        if (started)
        {
            words.Add(word.ToString());
        }

        return [.. words];
    }
}

/// <summary>
/// Connects the terminal to the engine (PLAN M1.8): the running one when there is one, otherwise it starts the engine and waits for it to serve (discovery takes a minute
/// or two). Every connection passes the version handshake.
/// </summary>
/// <param name="dataFolder">The engine's data folder (where <c>api.json</c> is).</param>
/// <param name="starter">How to start the engine.</param>
/// <param name="clientFor">Makes a client for an <see cref="ApiInfo"/> (tests pass a fake).</param>
/// <param name="delay">How a wait is made (tests pass one that returns at once).</param>
public sealed class EngineConnector(string dataFolder, IEngineStarter starter, Func<ApiInfo, IIkarsApi> clientFor, Func<TimeSpan, CancellationToken, Task>? delay = null)
{
    private readonly Func<TimeSpan, CancellationToken, Task> _delay = delay ?? Task.Delay;

    /// <summary>How long to wait for a started engine to serve.</summary>
    public TimeSpan StartTimeout { get; init; } = TimeSpan.FromMinutes(5);

    /// <summary>How often to look while waiting.</summary>
    public TimeSpan Poll { get; init; } = TimeSpan.FromSeconds(2);

    /// <summary>A client for the engine as it is now, after the handshake; it does not start one.</summary>
    /// <param name="cancellationToken">Cancels it.</param>
    /// <returns>The client.</returns>
    /// <exception cref="EngineUnavailableException">No engine is serving.</exception>
    /// <exception cref="IncompatibleEngineException">The engine speaks another major version.</exception>
    public async Task<IIkarsApi> ConnectAsync(CancellationToken cancellationToken)
    {
        var info = ApiInfo.Read(dataFolder) ?? throw new EngineUnavailableException("the engine is not running");
        var api = clientFor(info);
        try
        {
            var hello = await api.HelloAsync(cancellationToken).ConfigureAwait(false);
            return Handshake.Compatible(hello.ApiVersion) ? api : throw new IncompatibleEngineException(hello.ApiVersion);
        }
        catch
        {
            (api as IDisposable)?.Dispose();   // never handed out: dropped here
            throw;
        }
    }

    /// <summary>Connects, starting the engine first when none is serving.</summary>
    /// <param name="progress">Lines for the person while it waits.</param>
    /// <param name="cancellationToken">Cancels it.</param>
    /// <returns>The client.</returns>
    public async Task<IIkarsApi> ConnectOrStartAsync(Action<string> progress, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(progress);
        try
        {
            return await ConnectAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is EngineUnavailableException or ApiException)
        {
            // not running (or a stale api.json): start it below
        }

        if (!starter.Start())
        {
            throw new EngineUnavailableException("the engine is not running and no start command is registered (run: python -m ikars_engine install-autostart)");
        }

        progress("starting the engine (discovery takes a minute or two)");
        for (var waited = TimeSpan.Zero; waited < StartTimeout; waited += Poll)
        {
            await _delay(Poll, cancellationToken).ConfigureAwait(false);
            try
            {
                return await ConnectAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is EngineUnavailableException or ApiException)
            {
                progress("waiting for the engine to serve");
            }
        }

        throw new EngineUnavailableException($"the engine did not start within {StartTimeout.TotalMinutes:0} minutes; see its log");
    }
}
