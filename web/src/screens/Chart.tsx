import { ColorType, LineSeries, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { ChartPoint } from "../view/market";

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** A market's traded price (TradingView Lightweight Charts, Apache-2.0). Its own attribution logo is
 * off because it injects an inline style the engine's CSP refuses; the credit and link its licence asks
 * for are drawn below the chart instead. The chart is made once per screen and removed when left. */
export function PriceChart({ points }: { points: readonly ChartPoint[] }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ReturnType<IChartApi["addSeries"]> | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const made = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: token("--sub"), attributionLogo: false },
      grid: { vertLines: { color: token("--line") }, horzLines: { color: token("--line") } },
      rightPriceScale: { borderColor: token("--line") },
      timeScale: { borderColor: token("--line"), timeVisible: true },
      localization: { priceFormatter: (cents: number) => `${cents.toFixed(1)}¢` },
    });
    series.current = made.addSeries(LineSeries, { color: token("--accent"), lineWidth: 2 });
    chart.current = made;
    return () => {
      made.remove();
      chart.current = series.current = null;
    };
  }, []);

  useEffect(() => {
    series.current?.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    chart.current?.timeScale().fitContent();
  }, [points]);

  return (
    <>
      <div className="chart" ref={box} data-testid="price-chart" />
      <p className="muted small credit">
        Chart by{" "}
        <a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noopener noreferrer">
          TradingView Lightweight Charts
        </a>
      </p>
    </>
  );
}
