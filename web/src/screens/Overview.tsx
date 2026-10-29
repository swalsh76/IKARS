import { useEffect, useRef, useState, type MouseEvent } from "react";
import { answer, copyOrder } from "../app/answers";
import { useNow, useSnapshot, useTerminal, useValue } from "../app/hooks";
import { href } from "../nav/route";
import { latestAlerts, portfolioCard } from "../view/portfolio";
import type { RecommendationView } from "../api/contracts";
import { recommendationRow, scoreRows } from "../view/recommendations";
import { Guarded } from "./Guarded";
import { ENTER_EVENT, MOVE_EVENT, useAnswerKeys } from "./Keys";
import { Later, Link, plainClick } from "./Shell";

function Portfolio() {
  const portfolio = useValue("portfolio");
  if (portfolio === null) {
    return (
      <section className="card" aria-labelledby="portfolio">
        <h2 id="portfolio">Portfolio and exposure</h2>
        <p className="muted">Waiting for the engine…</p>
      </section>
    );
  }
  const card = portfolioCard(portfolio);
  return (
    <section className="card" aria-labelledby="portfolio">
      <h2 id="portfolio">Portfolio and exposure</h2>
      <div className="stats">
        <div>
          <div className="muted small">Cash</div>
          <div className="big" data-testid="cash">{card.cash}</div>
        </div>
        <div>
          <div className="muted small">Unrealised, after exit costs</div>
          <div className={`big ${card.unrealisedLevel}`}>{card.unrealised}</div>
        </div>
        <div>
          <div className="muted small">Open positions</div>
          <div className="big" data-testid="positions">{card.positions}</div>
        </div>
      </div>
      <div className="exposure">
        <div className="muted small">Exposure by category</div>
        {card.exposure.length === 0 && <div className="muted small">Nothing held.</div>}
        {card.exposure.map((e) => (
          <div className="bar-row" key={e.name}>
            <span>{e.name}</span>
            <div className="bar">
              <div className="fill" style={{ width: `${e.width}%` }} />
            </div>
            <span className="sub">{e.amount}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Alerts() {
  const alerts = latestAlerts(useValue("alerts"));
  return (
    <section className="card" aria-labelledby="alerts">
      <h2 id="alerts">Alerts</h2>
      {alerts.length === 0 && <p className="muted small">No alerts.</p>}
      {alerts.map((a) => (
        <div key={a.line} className={`alert small priority-${a.priority}`}>
          {a.priority} · {a.title}: {a.text}
        </div>
      ))}
      <Link to="/alerts" className="small">
        All alerts →
      </Link>
    </section>
  );
}

const rowLink = (id: string) => `rec-${encodeURIComponent(id)}`; // a valid element id whatever the id holds
const FIRST = "Highlight a recommendation first (Up, Down).";

/** The ranked list (M1.12d, owner 2026-10-28): a click anywhere on a row opens its recommendation; Up and
 * Down highlight a row (its link takes the focus) and Enter, T, I, D and C act on it. The highlight
 * follows the recommendation, not the place, so a re-ranked list never moves it to another. */
function TopRecommendations() {
  const page = useValue("recommendations");
  const now = useNow();
  const { navigator } = useTerminal();
  const table = useRef<HTMLTableElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  // the engine's copy after an answer, until the next list (so the same key again takes it back: merge review 1)
  const [kept, setKept] = useState<ReadonlyMap<string, RecommendationView>>(new Map());
  const [seen, setSeen] = useState(page);
  if (page !== seen) {
    setSeen(page);
    setKept(new Map());
  }
  const items = (page?.items ?? []).map((r) => kept.get(r.id) ?? r);
  const chosen = items.find((r) => r.id === selected) ?? null;
  const latest = useRef({ items, chosen });
  useEffect(() => {
    latest.current = { items, chosen };
  });
  useEffect(() => {
    const onMove = (event: Event) => {
      const { items, chosen } = latest.current;
      const focus = document.activeElement;
      const main = table.current?.closest("main");
      // the focus elsewhere on the Overview (another card's link) keeps the arrows the browser's; in the list,
      // the top bar (Back) or nowhere, they are the list's (merge review 3)
      if (items.length === 0 || (focus && main?.contains(focus) && !table.current?.contains(focus))) return;
      event.preventDefault();
      const at = chosen ? items.indexOf(chosen) : -1;
      const next = items[at < 0 ? 0 : Math.min(items.length - 1, Math.max(0, at + (event as CustomEvent<number>).detail))];
      if (!next) return;
      setSelected(next.id);
      document.getElementById(rowLink(next.id))?.focus();
    };
    const onEnter = (event: Event) => {
      const r = latest.current.chosen; // highlighted, though the focus has moved off it (merge review 2)
      if (!r) return;
      event.preventDefault();
      navigator.go(href({ screen: "recommendation", id: r.id }));
    };
    window.addEventListener(MOVE_EVENT, onMove);
    window.addEventListener(ENTER_EVENT, onEnter);
    return () => {
      window.removeEventListener(MOVE_EVENT, onMove);
      window.removeEventListener(ENTER_EVENT, onEnter);
    };
  }, [navigator]);
  useAnswerKeys(
    (given) => {
      const r = chosen;
      if (!r) return setNote(FIRST);
      void answer(r, given).then(({ value, note }) => {
        if (value) setKept((last) => new Map(last).set(value.id, value));
        setNote(`${r.title || r.ticker}: ${note}`);
      });
    },
    () => {
      if (!chosen) return setNote(FIRST);
      void copyOrder(chosen).then(setNote);
    },
  );
  const open = (event: MouseEvent<HTMLTableRowElement>, id: string) => {
    // the row's own link has moved already; a modified click or a text selection is left alone
    if (event.defaultPrevented || !plainClick(event) || window.getSelection()?.toString()) return;
    navigator.go(href({ screen: "recommendation", id }));
  };
  return (
    <section className="card" aria-labelledby="recs">
      <div className="card-head">
        <h2 id="recs">Top recommendations</h2>
        <span className="muted small" data-testid="active">
          {page === null ? "" : `${page.total} active`}
        </span>
      </div>
      <div className="muted small">Ranked by return per day of the money laid out · after fees · recommend-only: you place orders on Kalshi</div>
      {page === null && <p className="muted small">Waiting for the engine…</p>}
      {page !== null && page.items.length === 0 && <p className="muted small">No active recommendations.</p>}
      {page !== null && page.items.length > 0 && (
        <div className="scroll recs">
          <table aria-label="Top recommendations" ref={table}>
            <thead>
              <tr>
                <th>Market</th>
                <th>Action</th>
                <th className="num">Size</th>
                <th className="num">P&amp;L at target</th>
                <th className="num">Return/day</th>
                <th>Strategy</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((r) => {
                const row = recommendationRow(r, now);
                return (
                  <tr key={row.id} className={row.id === chosen?.id ? "pick on" : "pick"} onClick={(e) => open(e, row.id)} onFocus={() => setSelected(row.id)}>
                    <td>
                      <Link to={href({ screen: "recommendation", id: row.id })} id={rowLink(row.id)}>
                        {row.market}
                      </Link>
                      <div className="muted small">{row.why}</div>
                    </td>
                    <td className="nowrap">
                      {row.action}
                      {row.answer && <div className="muted small">{row.answer}</div>}
                    </td>
                    <td className="num">{row.size}</td>
                    <td className={`num ${row.pnlLevel}`}>{row.pnl}</td>
                    <td className="num">{row.perDay}</td>
                    <td className="nowrap">{row.strategy}</td>
                    <td className="nowrap">{row.expires}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="muted small hints">
        {items.length > 0 && (
          <>
            <span>Click a row to open it</span>
            <span>
              <kbd>Up</kbd> <kbd>Down</kbd> highlight · <kbd>Enter</kbd> open
            </span>
            <span>
              On the highlighted row: <kbd>C</kbd> copy order · <kbd>T</kbd> taken · <kbd>I</kbd> ignore · <kbd>D</kbd> dismiss
            </span>
          </>
        )}
        {/* shown with an empty list too: a key pressed then still says something (merge review 5) */}
        <span role="status" data-testid="list-note" className="note">
          {note}
        </span>
      </div>
    </section>
  );
}

function Scorecard() {
  const card = useSnapshot("/v1/scorecard", "ScoreCard", 60_000);
  return (
    <section className="card" aria-labelledby="score">
      <h2 id="score">Journal scorecard (30 days)</h2>
      {card.state === "loading" && <p className="muted small">Loading…</p>}
      {card.state === "failed" && <p className="muted small">{card.message}</p>}
      {card.state === "ready" && card.value.lines.length === 0 && (
        <p className="muted small">{card.value.at_utc === null ? "Being worked out…" : "No checks in the last 30 days."}</p>
      )}
      {card.state === "ready" && card.value.lines.length > 0 && (
        <table aria-label="Scorecard">
          <thead>
            <tr>
              <th>Rule</th>
              <th>After</th>
              <th className="num">Scored</th>
              <th className="num">Above 0</th>
              <th className="num">Mean</th>
              <th className="num">Median</th>
            </tr>
          </thead>
          <tbody>
            {scoreRows(card.value).map((s) => (
              <tr key={`${s.rule} ${s.horizon}`}>
                <td className="nowrap">{s.rule}</td>
                <td>{s.horizon}</td>
                <td className="num">{s.scored}</td>
                <td className="num">{s.above}</td>
                <td className={`num ${s.meanLevel}`}>{s.mean}</td>
                <td className="num">{s.median}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <span className="muted small">P&amp;L per recommendation after costs, at each check</span>
    </section>
  );
}

export function Overview() {
  return (
    <div className="grid">
      <div className="span-2">
        <Guarded title="Top recommendations">
          <TopRecommendations />
        </Guarded>
      </div>
      <Guarded title="Portfolio and exposure">
        <Portfolio />
      </Guarded>
      <Later title="Live movers (5 min)" slice="M2" note="It needs a scan of every market, not the journal." />
      <Later title="Latest source items" slice="M2" />
      <Guarded title="Journal scorecard (30 days)">
        <Scorecard />
      </Guarded>
      <Later title="Perp funding" slice="M3" note="Research slice M1.10 first." />
      <Later title="Book signals" slice="M3" />
      <Guarded title="Alerts">
        <Alerts />
      </Guarded>
    </div>
  );
}
