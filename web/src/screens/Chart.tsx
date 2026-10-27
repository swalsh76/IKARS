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

  const drawn = useRef<string>("");

  useEffect(() => {
    if (!box.current) return;
    const made = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, attributionLogo: false },
      timeScale: { timeVisible: true },
      localization: { priceFormatter: (cents: number) => `${cents.toFixed(1)}¢` },
    });
    const line = made.addSeries(LineSeries, { lineWidth: 2 });
    // the colours follow the theme, also when Windows switches it while the chart is open
    const paint = () => {
      made.applyOptions({
        layout: { textColor: token("--sub") },
        grid: { vertLines: { color: token("--line") }, horzLines: { color: token("--line") } },
        rightPriceScale: { borderColor: token("--line") },
        timeScale: { borderColor: token("--line") },
      });
      line.applyOptions({ color: token("--accent") });
    };
    paint();
    const scheme = window.matchMedia("(prefers-color-scheme: light)");
    scheme.addEventListener("change", paint);
    series.current = line;
    chart.current = made;
    return () => {
      scheme.removeEventListener("change", paint);
      made.remove();
      chart.current = series.current = null;
      drawn.current = "";
    };
  }, []);

  useEffect(() => {
    // a refresh with the same candles changes nothing, and only the first drawing fits the view, so
    // the owner's zoom stays (merge review 5)
    const last = points.at(-1);
    const shape = `${points.length}:${last?.time ?? 0}:${last?.value ?? 0}`;
    if (shape === drawn.current) return;
    const first = drawn.current === "";
    drawn.current = shape;
    series.current?.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    if (first) chart.current?.timeScale().fitContent();
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
