using System.Diagnostics;
using System.Diagnostics.CodeAnalysis;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using Ikars.Terminal.Shell;

namespace Ikars.Terminal.Views;

/// <summary>
/// The main window: it flushes the stream's changes about ten times a second (PLAN D3) and times each change to the frame that shows it (J). It listens for
/// WPF's next frame only while a change waits for one, so an idle terminal does not keep WPF's render loop running.
/// </summary>
[ExcludeFromCodeCoverage(Justification = "a WPF view; its logic is in ShellViewModel")]
internal partial class MainWindow : Window
{
    private readonly DispatcherTimer _tick = new() { Interval = TimeSpan.FromMilliseconds(100) };
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private ShellViewModel? _shell;
    private bool _listening;

    public MainWindow() => InitializeComponent();

    public async void Start(ShellViewModel shell)
    {
        _shell = shell;
        _tick.Tick += async (_, _) =>
        {
            var began = _clock.Elapsed;
            if (await shell.TickAsync().ConfigureAwait(true))
            {
                UpdateLayout();   // the layout the change caused, counted as its work
                shell.Frames.Changed(began, _clock.Elapsed - began);
                Listen(true);
            }
        };
        Closed += (_, _) =>
        {
            _tick.Stop();
            Listen(false);
            shell.Stop();
            shell.Dispose();
        };
        try
        {
            await shell.StartAsync().ConfigureAwait(true);
            _tick.Start();
        }
        catch (Exception ex)
        {
            shell.Problem = ex.Message;
        }
    }

    private void Listen(bool on)
    {
        if (on != _listening)
        {
            _listening = on;
            if (on)
            {
                CompositionTarget.Rendering += OnRendering;
            }
            else
            {
                CompositionTarget.Rendering -= OnRendering;
            }
        }
    }

    private void OnRendering(object? sender, EventArgs e)
    {
        _shell?.Frames.Frame(_clock.Elapsed);
        Listen(_shell?.Frames.Waiting ?? false);
    }
}
