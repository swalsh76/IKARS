import { useState } from "react";
import { useSnapshot } from "../app/hooks";
import { href } from "../nav/route";
import { recordRow } from "../view/market";
import { ledgerLines } from "../view/records";
import { Guarded } from "./Guarded";
import { Link } from "./Shell";

const PAGE = 100;

function Status() {
  const status = useSnapshot("/v1/ledger/status", "LedgerStatusView", 60_000);
  return (
    <section className="card" aria-label="Ledger status">
      <h2>Ledger</h2>
      {status.state === "loading" && <p className="muted small">Loading…</p>}
      {status.state === "failed" && <p className="muted small">{status.message}</p>}
      {status.state === "ready" &&
        ledgerLines(status.value).map((line) => (
          <div key={line} className="sub small">
            {line}
          </div>
        ))}
      <p className="muted small">Your own records, built from Kalshi&apos;s account records; kept on this workstation only.</p>
    </section>
  );
}

function Records() {
  const [ticker, setTicker] = useState("");
  const [filter, setFilter] = useState("");
  const [pages, setPages] = useState<number[]>([]); // the `before` of each older page opened
  const before = pages.at(-1);
  const params = new URLSearchParams({ limit: String(PAGE) });
  if (before !== undefined) params.set("before", String(before));
  if (filter) params.set("ticker", filter);
  const page = useSnapshot(`/v1/ledger/records?${params.toString()}`, "LedgerPage");
  return (
    <section className="card" aria-label="Ledger records">
      <div className="toolbar">
        <h2>Records</h2>
        <form
          role="search"
          onSubmit={(event) => {
            event.preventDefault();
            setFilter(ticker.trim());
            setPages([]);
          }}
        >
          <label htmlFor="ledger-ticker" className="hidden">
            Market ticker
          </label>
          <input id="ledger-ticker" type="search" placeholder="Market ticker (Enter)" value={ticker} onChange={(e) => setTicker(e.target.value)} />
        </form>
        <button type="button" disabled={pages.length === 0} onClick={() => setPages(pages.slice(0, -1))}>
          Newer
        </button>
        <button
          type="button"
          disabled={page.state !== "ready" || page.value.before === null}
          onClick={() => {
            if (page.state === "ready" && page.value.before !== null) setPages([...pages, page.value.before]);
          }}
        >
          Older
        </button>
      </div>
      {page.state === "loading" && <p className="muted small">Loading…</p>}
      {page.state === "failed" && <p className="muted small">{page.message}</p>}
      {page.state === "ready" && page.value.items.length === 0 && <p className="muted small">No records.</p>}
      {page.state === "ready" && page.value.items.length > 0 && (
        <div className="scroll">
          <table aria-label="Records">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>When</th>
                <th>Kind</th>
                <th>Market</th>
                <th className="num">Cash</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {page.value.items.map((record) => {
                const row = recordRow(record);
                return (
                  <tr key={record.seq}>
                    <td className="num">{record.seq}</td>
                    <td>{row.when}</td>
                    <td>{row.kind}</td>
                    <td>{record.ticker ? <Link to={href({ screen: "market", ticker: record.ticker })}>{record.ticker}</Link> : ""}</td>
                    <td className="num">{row.cash}</td>
                    <td>{row.note}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function Ledger() {
  return (
    <div className="grid">
      <Guarded title="Ledger">
        <Status />
      </Guarded>
      <div className="span-2">
        <Guarded title="Records">
          <Records />
        </Guarded>
      </div>
    </div>
  );
}
