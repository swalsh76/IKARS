namespace Ikars.Terminal.Live;

/// <summary>
/// Frame time (PLAN D20 J; owner's target, as amended 2026-10-27: an update reaches the screen within <see cref="TargetP99Ms"/> at p99 while 100 markets
/// stream). For each tick that changed the screen: the UI thread's work (the hand-over and its layout) and the time from the tick until WPF starts the frame
/// <em>after</em> the one that shows it, by when that frame has gone to the display (an upper bound, conservative by up to a refresh); the last
/// <see cref="Window"/> of each, as percentiles. Gaps between frames are not measured: WPF draws only when something changed, so an idle gap is not a
/// slow frame (M1.8a, measured at the console).
/// </summary>
internal sealed class FrameMeter
{
    /// <summary>How many updates the percentiles cover.</summary>
    public const int Window = 600;

    /// <summary>The owner's target for p99 update-to-screen time, in milliseconds (two refreshes at 60 Hz).</summary>
    public const double TargetP99Ms = 33.0;

    private readonly Samples _toScreen = new();
    private readonly Samples _work = new();
    private TimeSpan? _pending;
    private int _framesSince;

    /// <summary>A tick that began at <paramref name="began"/> changed the screen, and the UI thread spent <paramref name="work"/> on it.</summary>
    public void Changed(TimeSpan began, TimeSpan work)
    {
        _work.Add(work.TotalMilliseconds);
        _pending ??= began;   // a change still waiting for its frame keeps the earlier time
    }

    /// <summary>Whether a change is waiting for its frame (the window listens for the next frame only then).</summary>
    public bool Waiting => _pending is not null;

    /// <summary>
    /// WPF starts a frame at <paramref name="at"/> (the same clock as <see cref="Changed"/>). The first after a change draws it; the second means the first has gone
    /// to the display, and the change is timed.
    /// </summary>
    public void Frame(TimeSpan at)
    {
        if (_pending is { } began && ++_framesSince == 2)
        {
            _toScreen.Add(Math.Max(0, (at - began).TotalMilliseconds));
            _pending = null;
            _framesSince = 0;
        }
    }

    /// <summary>The updates measured (up to <see cref="Window"/>).</summary>
    public int Count => _toScreen.Count;

    /// <summary>The median update-to-screen time in milliseconds, or <see langword="null"/> before the first.</summary>
    public double? P50 => _toScreen.Percentiles()?.P50;

    /// <summary>The 99th-percentile update-to-screen time in milliseconds, or <see langword="null"/> before the first.</summary>
    public double? P99 => _toScreen.Percentiles()?.P99;

    /// <summary>The 99th-percentile UI work per update in milliseconds, or <see langword="null"/> before the first.</summary>
    public double? WorkP99 => _work.Percentiles()?.P99;

    /// <summary>Whether p99 is within the target.</summary>
    public bool WithinTarget => P99 is { } p99 && p99 <= TargetP99Ms;

    /// <summary>One line for the status bar.</summary>
    public string Text => _toScreen.Percentiles() is { } p
        ? $"to screen p50 {p.P50:0} ms, p99 {p.P99:0} ms · work p99 {WorkP99:0} ms"
        : "to screen: measuring";

    /// <summary>The last <see cref="Window"/> values, as p50 and p99 (nearest rank).</summary>
    private sealed class Samples
    {
        private readonly double[] _values = new double[Window];
        private int _next;

        public int Count { get; private set; }

        public void Add(double value)
        {
            _values[_next] = value;
            _next = (_next + 1) % Window;
            Count = Math.Min(Count + 1, Window);
        }

        public (double P50, double P99)? Percentiles()
        {
            if (Count == 0)
            {
                return null;
            }

            var sorted = _values.AsSpan(0, Count).ToArray();
            Array.Sort(sorted);
            return (At(50), At(99));

            double At(double p) => sorted[Math.Clamp((int)Math.Ceiling(p / 100.0 * sorted.Length) - 1, 0, sorted.Length - 1)];
        }
    }
}
