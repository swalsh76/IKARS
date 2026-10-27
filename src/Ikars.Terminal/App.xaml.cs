using System.Diagnostics.CodeAnalysis;
using System.Windows;
using Ikars.Client;
using Ikars.Terminal.Live;
using Ikars.Terminal.Shell;
using Ikars.Terminal.Views;

namespace Ikars.Terminal;

/// <summary>The terminal's start: the main window, connected to the engine (started when none runs).</summary>
[ExcludeFromCodeCoverage(Justification = "WPF start-up; the logic is in ShellViewModel and Ikars.Client, which are tested")]
internal partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // --data-folder <path>: another engine's data folder (a portable install, or a load test)
        var at = Array.IndexOf(e.Args, "--data-folder");
        var folder = at >= 0 && at + 1 < e.Args.Length ? e.Args[at + 1] : ApiInfo.DefaultDataFolder;
        var connector = new EngineConnector(folder, new RegisteredEngineStarter(), info => new IkarsApiClient(info));
        var shell = new ShellViewModel(connector, new FrameMeter());
        var window = new MainWindow { DataContext = shell };
        MainWindow = window;
        window.Show();
        window.Start(shell);
    }
}
