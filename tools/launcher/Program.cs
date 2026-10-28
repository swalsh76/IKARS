// IKARS.exe (M1.9a, PLAN D12): starts the bundled Python with IKARS's launcher module, passing every
// argument through (Velopack runs the main exe with its own install, update and uninstall steps), and
// returns its exit code. Nothing else: the work is in ikars_engine.launcher.

using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

internal static class Program
{
    private const uint MbIconError = 0x10;

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr owner, string text, string caption, uint type);

    [STAThread]
    private static int Main(string[] args)
    {
        string here = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(here, "python", "pythonw.exe");
        if (!File.Exists(python))
        {
            MessageBoxW(IntPtr.Zero, "IKARS's Python is missing from " + here + ": reinstall IKARS.", "IKARS", MbIconError);
            return 2;
        }
        var line = new StringBuilder("-I -m ikars_engine.launcher"); // -I: no user site or PYTHON* variables
        foreach (string arg in args)
        {
            line.Append(' ').Append(Quote(arg));
        }
        var start = new ProcessStartInfo(python, line.ToString())
        {
            UseShellExecute = false,
            WorkingDirectory = here,
        };
        using Process child = Process.Start(start)!;
        child.WaitForExit();
        return child.ExitCode;
    }

    /// <summary>One argument as Windows' command-line parser reads it back (backslashes before a quote
    /// doubled, the whole quoted when it holds a space, tab or quote).</summary>
    internal static string Quote(string arg)
    {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
        {
            return arg;
        }
        var quoted = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\')
            {
                slashes++;
                continue;
            }
            quoted.Append('\\', c == '"' ? slashes * 2 + 1 : slashes).Append(c);
            slashes = 0;
        }
        return quoted.Append('\\', slashes * 2).Append('"').ToString();
    }
}
