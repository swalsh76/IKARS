import { useValue } from "../app/hooks";
import { latestAlerts, portfolioCard } from "../view/portfolio";
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

export function Overview() {
  return (
    <div className="grid">
      <div className="span-2">
        <Later title="Top recommendations" slice="M1.12" note="Ranked by return per day of capital tied up, after fees, spread and slippage; recommend-only." />
      </div>
      <Guarded title="Portfolio and exposure">
        <Portfolio />
      </Guarded>
      <Later title="Live movers (5 min)" slice="M1.12" />
      <Later title="Latest source items" slice="M2" />
      <Later title="Journal scorecard (30 days)" slice="M1.12" />
      <Later title="Perp funding" slice="M3" note="Research slice M1.10 first." />
      <Later title="Book signals" slice="M3" />
      <Guarded title="Alerts">
        <Alerts />
      </Guarded>
    </div>
  );
}
