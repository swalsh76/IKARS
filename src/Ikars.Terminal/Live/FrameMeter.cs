namespace Ikars.Terminal.Live;

/// <summary>
/// Frame time (PLAN D20 J; owner's target: p99 at most 33 ms while 100 markets stream): the gaps between WPF's rendered frames, kept for the last
/// <see cref="Window"/> frames, as p50 and p99.
/// </summary>
internal sealed class FrameMeter
{
    /// <summary>How many frames the percentiles cover.</summary>
    public const int Window = 600;

    /// <summary>The owner's target for p99, in milliseconds.</summary>
    public const double TargetP99Ms = 33.0;

    private readonly double[] _gaps = new double[Window];
    private int _count;
    private int _next;
    private TimeSpan? _last;

    /// <summary>A frame was rendered at <paramref name="renderingTime"/> (WPF's RenderingEventArgs.RenderingTime). WPF repeats a time when it skips a frame; that is not a frame.</summary>
    public void Frame(TimeSpan renderingTime)
    {
        if (_last is { } last && renderingTime > last)
        {
            _gaps[_next] = (renderingTime - last).TotalMilliseconds;
            _next = (_next + 1) % Window;
            _count = Math.Min(_count + 1, Window);
        }

        if (_last is null || renderingTime > _last)
        {
            _last = renderingTime;
        }
    }

    /// <summary>The frames measured (up to <see cref="Window"/>).</summary>
    public int Count => _count;

    /// <summary>The median gap in milliseconds, or <see langword="null"/> before two frames.</summary>
    public double? P50 => Percentile(50);

    /// <summary>The 99th-percentile gap in milliseconds, or <see langword="null"/> before two frames.</summary>
    public double? P99 => Percentile(99);

    /// <summary>Whether p99 is within the target.</summary>
    public bool WithinTarget => P99 is { } p99 && p99 <= TargetP99Ms;

    /// <summary>One line for the status bar.</summary>
    public string Text => P99 is { } p99 ? $"frame p50 {P50:0} ms, p99 {p99:0} ms" : "frame time: measuring";

    private double? Percentile(double p)
    {
        if (_count == 0)
        {
            return null;
        }

        var sorted = _gaps.AsSpan(0, _count).ToArray();
        Array.Sort(sorted);
        var rank = (int)Math.Ceiling(p / 100.0 * _count) - 1;
        return sorted[Math.Clamp(rank, 0, _count - 1)];
    }
}
