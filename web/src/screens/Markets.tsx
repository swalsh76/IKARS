import { CellStyleModule, ClientSideRowModelModule, ModuleRegistry, RowStyleModule, type ColDef, type RowClickedEvent } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import { AgGridReact } from "ag-grid-react";
import { useEffect, useMemo, useState } from "react";
import type { QuoteUpdate } from "../api/contracts";
import { useMarkets, useSnapshot, useTerminal } from "../app/hooks";
import { href } from "../nav/route";
import { cachedRow, categories, type MarketRow } from "../view/market";

// Only the grid's parts in use (a smaller bundle). AG Grid's CSS-file theme ("legacy"), not its
// injected one: the engine's CSP allows no inline style (M1.8a-web-2a).
ModuleRegistry.registerModules([ClientSideRowModelModule, RowStyleModule, CellStyleModule]);

const PAGE = 200; // at most the engine's subscriptions per stream
const SEARCH_WAIT_MS = 300;

const right = { cellClass: "num", headerClass: "num" };
const columns: ColDef<MarketRow>[] = [
  { field: "ticker", headerName: "Ticker", width: 230 },
  { field: "title", headerName: "Market", flex: 1, minWidth: 220 },
  { field: "category", headerName: "Category", width: 130 },
  { field: "bid", headerName: "Bid", width: 80, ...right },
  { field: "ask", headerName: "Ask", width: 80, ...right },
  { field: "last", headerName: "Last", width: 80, ...right },
  { field: "volume", headerName: "Vol 24 h", width: 110, ...right },
  { field: "closes", headerName: "Closes", width: 130 },
  { field: "watched", headerName: "Book", width: 80 },
];

export function Markets({ query, category = "" }: { query: string; category?: string | undefined }) {
  const { hub, navigator } = useTerminal();
  const [text, setText] = useState(query);
  const [seen, setSeen] = useState(query);
  if (query !== seen) {
    // the address changed from outside (the header's search, back or forward): show its query
    setSeen(query);
    if (query !== text.trim()) setText(query);
  }
  useEffect(() => {
    const wait = setTimeout(() => navigator.replace(href({ screen: "markets", query: text.trim(), category })), SEARCH_WAIT_MS);
    return () => clearTimeout(wait);
  }, [text, category, navigator]);
  const params = new URLSearchParams({ limit: String(PAGE) });
  if (query) params.set("q", query);
  if (category) params.set("category", category);
  const page = useSnapshot(`/v1/markets?${params.toString()}`, "MarketPage", 60_000);
  const items = useMemo(() => (page.state === "ready" ? page.value.items : []), [page]);
  const tickers = useMemo(() => items.map((m) => m.ticker), [items]);
  const changes = useMarkets(tickers);

  // a row object changes only when its market or its quote does, so the grid redraws just the rows
  // that moved, not all of them at every update (merge review 4)
  const rows = useMemo(
    () => items.map((market) => cachedRow(market, hub.get(`quote:${market.ticker}`) as QuoteUpdate | undefined)),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `changes` counts the quotes' updates
    [items, hub, changes],
  );

  // every category seen, so choosing one does not hide the others (merge review 6)
  const [known, setKnown] = useState<string[]>([]);
  const found = useMemo(() => categories(items), [items]);
  if (found.some((c) => !known.includes(c))) setKnown([...new Set([...known, ...found])].sort((a, b) => a.localeCompare(b)));
  const options = category && !known.includes(category) ? [category, ...known] : known;

  return (
    <section className="card markets" aria-label="Markets">
      <div className="toolbar">
        <h2>Markets</h2>
        <label htmlFor="market-search" className="hidden">
          Search markets
        </label>
        <input id="market-search" type="search" placeholder="Ticker or title" value={text} onChange={(e) => setText(e.target.value)} />
        <label htmlFor="market-category" className="hidden">
          Category
        </label>
        <select
          id="market-category"
          value={category}
          onChange={(e) => navigator.replace(href({ screen: "markets", query: text.trim(), category: e.target.value }))}
        >
          <option value="">All categories</option>
          {options.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <span className="muted small" data-testid="market-count">
          {page.state === "ready"
            ? `${page.value.total} found${page.value.total > PAGE ? `, first ${PAGE} shown` : ""}${page.stale ? ` (not refreshed: ${page.stale})` : ""}`
            : page.state === "failed"
              ? page.message
              : "Loading…"}
        </span>
      </div>
      {page.state === "ready" && page.value.total === 0 && <p className="muted small">No markets match.</p>}
      <div className="grid-box ag-theme-quartz">
        <AgGridReact<MarketRow>
          theme="legacy"
          rowData={rows}
          columnDefs={columns}
          getRowId={(p) => p.data.ticker}
          onRowClicked={(e: RowClickedEvent<MarketRow>) => e.data && navigator.go(href({ screen: "market", ticker: e.data.ticker }))}
          rowClass="clickable"
          suppressNoRowsOverlay
          loading={false}
        />
      </div>
    </section>
  );
}
