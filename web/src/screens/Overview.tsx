import { useNow, useSnapshot, useValue } from "../app/hooks";
import { href } from "../nav/route";
import { latestAlerts, portfolioCard } from "../view/portfolio";
import { recommendationRow, scoreRows } from "../view/recommendations";
import { Guarded } from "./Guarded";
import { Later, Link } from "./Shell";

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

function TopRecommendations() {
  const page = useValue("recommendations");
  const now = useNow();
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
          <table aria-label="Top recommendations">
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
                  <tr key={row.id}>
                    <td>
                      <Link to={href({ screen: "market", ticker: r.ticker })}>{row.market}</Link>
                      <div className="muted small">{row.why}</div>
                    </td>
                    <td className="nowrap">
                      <Link to={href({ screen: "recommendation", id: row.id })}>{row.action}</Link>
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
