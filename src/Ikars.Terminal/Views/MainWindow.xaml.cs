using System.Diagnostics.CodeAnalysis;
using System.Windows;
using System.Windows.Media;
using System.Windows.Threading;
using Ikars.Terminal.Shell;

namespace Ikars.Terminal.Views;

/// <summary>The main window: it flushes the stream's changes about ten times a second (PLAN D3) and feeds the frame meter from WPF's render loop (J).</summary>
[ExcludeFromCodeCoverage(Justification = "a WPF view; its logic is in ShellViewModel")]
internal partial class MainWindow : Window
{
    private readonly DispatcherTimer _tick = new() { Interval = TimeSpan.FromMilliseconds(100) };
    private ShellViewModel? _shell;

    public MainWindow() => InitializeComponent();

    public async void Start(ShellViewModel shell)
    {
        _shell = shell;
        CompositionTarget.Rendering += OnRendering;
        _tick.Tick += async (_, _) => await shell.TickAsync().ConfigureAwait(true);
        Closed += (_, _) =>
        {
            _tick.Stop();
            CompositionTarget.Rendering -= OnRendering;
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

    private void OnRendering(object? sender, EventArgs e)
    {
        if (e is RenderingEventArgs rendering && _shell is not null)
        {
            _shell.Frames.Frame(rendering.RenderingTime);
        }
    }
}
