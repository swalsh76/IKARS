import { useEffect, useMemo, useState } from "react";
import type { BookView, QuoteUpdate } from "../api/contracts";
import { useMarkets, useSnapshot, useTerminal } from "../app/hooks";
import { href } from "../nav/route";
import { bookRows, chartPoints, emptyChart, isOpen, marketRow, recordRow } from "../view/market";
import { PriceChart } from "./Chart";
import { Guarded } from "./Guarded";
import { BOOK_ID, LINKED_EVENT } from "./Keys";
import { Link } from "./Shell";

function Book({ book, watched }: { book: BookView | undefined; watched: boolean }) {
  if (!watched) return <p className="muted small">The engine keeps order books for watched markets only; this one is not watched.</p>;
  if (!book || !book.ready) return <p className="muted small">Waiting for the book…</p>;
  const { yes, no } = bookRows(book);
  const side = (title: string, rows: typeof yes) => (
    <table className="book" aria-label={title}>
      <thead>
        <tr>
          <th>{title}</th>
          <th className="num">Contracts</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.price}>
            <td>{r.price}</td>
            <td className="num">{r.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <div className="book-sides">
      {side("YES bids", yes)}
      {side("NO bids", no)}
    </div>
  );
}

export function Market({ ticker }: { ticker: string }) {
  const { hub } = useTerminal();
  const path = encodeURIComponent(ticker);
  const market = useSnapshot(`/v1/markets/${path}`, "MarketView", 60_000);
  const info = market.state === "ready" ? market.value : null;
  const watched = info?.watched ?? false;
  useMarkets([ticker], watched ? [ticker] : []);
  const quote = hub.get(`quote:${ticker}`) as QuoteUpdate | undefined;
  const book = hub.get(`book:${ticker}`) as BookView | undefined;
  const candles = useSnapshot(`/v1/markets/${path}/candles?interval=1&hours=24`, "CandlePage", 60_000);
  // the engine has no event filter: the event ticker as a search, up to a page, and say if it is cut
  // (merge review 7)
  const siblings = useSnapshot(info ? `/v1/markets?limit=200&q=${encodeURIComponent(info.event_ticker)}` : null, "MarketPage", 60_000);
  const records = useSnapshot(`/v1/ledger/records?limit=50&ticker=${path}`, "LedgerPage", 60_000);
  const points = useMemo(() => (candles.state === "ready" ? chartPoints(candles.value.items) : []), [candles]);
  const [linked, setLinked] = useState(true); // X shows or hides it
  useEffect(() => {
    const flip = () => setLinked((shown) => !shown);
    window.addEventListener(LINKED_EVENT, flip);
    return () => window.removeEventListener(LINKED_EVENT, flip);
  }, []);

  if (market.state === "failed") {
    return (
      <section className="card">
        <h2>{ticker}</h2>
        <p className="muted">{market.message}</p>
        <Link to="/markets">Back to the markets</Link>
      </section>
    );
  }
  if (!info) return <p className="muted">Loading {ticker}…</p>;
  const row = marketRow(info, quote);
  const empty = candles.state === "ready" ? emptyChart(candles.value.items) : null;
  return (
    <div className="grid">
      <section className="card span-3" aria-label="Quote">
        <div className="card-head">
          <h2>{info.title}</h2>
          <span className={`state ${isOpen(info.status) ? "level-ok" : "level-watch"}`}>{info.status}</span>
        </div>
        <div className="muted small">
          {ticker} · {row.category} · closes {row.closes}
        </div>
        <div className="stats quote">
          <div>
            <div className="muted small">YES bid</div>
            <div className="big" data-testid="bid">
              {row.bid}
            </div>
          </div>
          <div>
            <div className="muted small">YES ask</div>
            <div className="big" data-testid="ask">
              {row.ask}
            </div>
          </div>
          <div>
            <div className="muted small">Last</div>
            <div className="big">{row.last}</div>
          </div>
          <div>
            <div className="muted small">Traded, 24 h</div>
            <div className="big">{row.volume}</div>
          </div>
        </div>
      </section>
      <div className="span-2">
        <Guarded title="Price">
          <section className="card chart-card" aria-label="Price">
            <h2>YES price, last 24 hours</h2>
            {candles.state === "failed" && <p className="muted small">{candles.message}</p>}
            {empty && <p className="muted small">{empty}</p>}
            {candles.state === "ready" && !empty && <PriceChart points={points} />}
          </section>
        </Guarded>
      </div>
      <Guarded title="Order book">
        <section className="card" aria-label="Order book" id={BOOK_ID} tabIndex={-1}>
          <h2>Order book</h2>
          <Book book={book} watched={watched} />
        </section>
      </Guarded>
      <div className={`span-3${linked ? "" : " gone"}`}>
        <Guarded title="Linked">
          <section className="card" aria-label="Linked">
            <h2>Linked</h2>
            <div className="linked">
              <div>
                <div className="muted small">Same event</div>
                {siblings.state === "ready" &&
                  siblings.value.items
                    .filter((m) => m.ticker !== ticker && m.event_ticker === info.event_ticker)
                    .map((m) => (
                      <div key={m.ticker} className="small">
                        <Link to={href({ screen: "market", ticker: m.ticker })}>{m.title}</Link>
                      </div>
                    ))}
                {siblings.state === "ready" && siblings.value.total > siblings.value.items.length && (
                  <div className="muted small">
                    {siblings.value.items.length} of {siblings.value.total} matches shown
                  </div>
                )}
              </div>
              <div>
                <div className="muted small">Your ledger records</div>
                {records.state === "ready" && records.value.items.length === 0 && <div className="muted small">None.</div>}
                {records.state === "ready" &&
                  records.value.items.map((record) => recordRow(record)).map((r) => (
                    <div key={r.seq} className="small">
                      {r.when} · {r.kind} {r.cash} {r.note}
                    </div>
                  ))}
              </div>
              <div>
                <div className="muted small">Source items, recommendations, perps</div>
                <div className="muted small">Arrive in M1.12, M2 and M3.</div>
              </div>
            </div>
          </section>
        </Guarded>
      </div>
    </div>
  );
}
