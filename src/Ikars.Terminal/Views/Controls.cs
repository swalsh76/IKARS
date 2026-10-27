using System.ComponentModel;
using System.Diagnostics.CodeAnalysis;
using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using Ikars.Terminal.Screens;
using ScottPlot.WPF;

namespace Ikars.Terminal.Views;

/// <summary>Small converters for the screen templates.</summary>
[ExcludeFromCodeCoverage(Justification = "a WPF view helper")]
internal static class Converters
{
    public static readonly IValueConverter BoldWhen = new Lambda(value => value is true ? FontWeights.SemiBold : FontWeights.Normal);

    public static readonly IValueConverter WatchedMark = new Lambda(value => value is true ? "live" : string.Empty);

    private sealed class Lambda(Func<object?, object> convert) : IValueConverter
    {
        public object Convert(object? value, Type targetType, object? parameter, CultureInfo culture) => convert(value);

        public object ConvertBack(object? value, Type targetType, object? parameter, CultureInfo culture) => throw new NotSupportedException();
    }
}

/// <summary>The market's YES price for today (ScottPlot, as KalTerm3): redrawn whenever the screen's chart version moves.</summary>
[ExcludeFromCodeCoverage(Justification = "a WPF view; the points come from MarketScreen, which is tested")]
internal sealed class MarketChart : UserControl
{
    private readonly WpfPlot _plot = new();
    private readonly TextBlock _empty = new()
    {
        Text = "no trades today",
        HorizontalAlignment = HorizontalAlignment.Center,
        VerticalAlignment = VerticalAlignment.Center,
        Opacity = 0.7,
    };

    private MarketScreen? _screen;

    public MarketChart()
    {
        Content = new Grid { Children = { _plot, _empty } };
        MinHeight = 160;
        DataContextChanged += (_, _) => Attach(DataContext as MarketScreen);
    }

    private void Attach(MarketScreen? screen)
    {
        if (_screen is not null)
        {
            _screen.PropertyChanged -= OnChanged;
        }

        _screen = screen;
        if (_screen is not null)
        {
            _screen.PropertyChanged += OnChanged;
            Draw();
        }
    }

    private void OnChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(MarketScreen.ChartVersion))
        {
            Draw();
        }
    }

    private void Draw()
    {
        var plot = _plot.Plot;
        plot.Clear();
        var points = _screen?.ChartPoints ?? [];

        // no axes without data: an empty plot reads as a broken chart
        _plot.Visibility = points.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        _empty.Visibility = points.Count > 0 ? Visibility.Collapsed : Visibility.Visible;
        if (points.Count > 0)
        {
            var times = points.Select(p => p.End.ToLocalTime().ToOADate()).ToArray();
            var cents = points.Select(p => p.Cents).ToArray();
            plot.Add.ScatterLine(times, cents);
            plot.Axes.DateTimeTicksBottom();
            plot.Axes.AutoScaleX();
            plot.Axes.SetLimitsY(0, 100);
        }

        _plot.Refresh();
    }
}
