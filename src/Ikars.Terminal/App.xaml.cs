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
        var connector = new EngineConnector(ApiInfo.DefaultDataFolder, new RegisteredEngineStarter(), info => new IkarsApiClient(info));
        var shell = new ShellViewModel(connector, new FrameMeter());
        var window = new MainWindow { DataContext = shell };
        MainWindow = window;
        window.Show();
        window.Start(shell);
    }
}
