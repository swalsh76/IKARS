import { useEffect, useRef, useState } from "react";
import type { RecommendationView } from "../api/contracts";
import { answer, copyOrder } from "../app/answers";
import { useRoute, useSnapshot, useTerminal } from "../app/hooks";
import { href } from "../nav/route";
import { ANSWERS, checkRows, figures, headline, inputText, ruleName, type Answer } from "../view/recommendations";
import { local } from "../view/health";
import { contracts, e2 } from "../money";
import { Guarded } from "./Guarded";
import { COPY_EVENT, RESPOND_EVENT } from "./Keys";
import { Link } from "./Shell";

const LABELS: Record<Answer, string> = { taken: "Mark taken", ignored: "Ignore", dismissed: "Dismiss" };

function Checks({ r }: { r: RecommendationView }) {
  const rows = checkRows(r);
  return (
    <section className="card" aria-label="Checks">
      <h2>Checks after fees</h2>
      <table aria-label="Checks">
        <thead>
          <tr>
            <th>After</th>
            <th>When</th>
            <th className="num">YES bid</th>
            <th className="num">YES ask</th>
            <th className="num">Score</th>
            <th>How</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.horizon}>
              <td>{c.horizon}</td>
              <td>{c.when}</td>
              <td className="num">{c.bid}</td>
              <td className="num">{c.ask}</td>
              <td className={`num ${c.level}`}>{c.score}</td>
              <td className="muted">{c.how}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Each check sells the {r.side.toUpperCase()} side at its bid then, after both fees; a market that had closed is held to its settlement.</p>
    </section>
  );
}

export function Recommendation({ id }: { id: string }) {
  const loaded = useSnapshot(`/v1/recommendations/${encodeURIComponent(id)}`, "RecommendationView", 15_000);
  const [answered, setAnswered] = useState<RecommendationView | null>(null);
  const [note, setNote] = useState("");
  const shown = answered ?? (loaded.state === "ready" ? loaded.value : null);
  const current = useRef(shown);
  useEffect(() => {
    current.current = shown;
  });
  const [seen, setSeen] = useState(loaded);
  if (loaded !== seen) {
    setSeen(loaded);
    if (loaded.state === "ready") setAnswered(null); // a newer copy from the engine wins
  }

  const respond = async (given: Answer | null) => {
    const r = current.current;
    if (!r) return;
    const { value, note } = await answer(r, given);
    if (value) setAnswered(value);
    setNote(note);
  };
  const copy = async () => {
    const r = current.current;
    if (r) setNote(await copyOrder(r));
  };
  const { navigator } = useTerminal();
  const route = useRoute(); // a move clears the name: given again after each one (per-commit review)
  const name = shown ? shown.title || shown.ticker : null;
  useEffect(() => {
    navigator.name(name); // the last crumb and the window title name the market
  }, [navigator, name, route]);
  const handlers = useRef({ respond, copy });
  useEffect(() => {
    handlers.current = { respond, copy };
  });
  useEffect(() => {
    const onRespond = (event: Event) => void handlers.current.respond((event as CustomEvent<Answer>).detail);
    const onCopy = () => void handlers.current.copy();
    window.addEventListener(RESPOND_EVENT, onRespond);
    window.addEventListener(COPY_EVENT, onCopy);
    return () => {
      window.removeEventListener(RESPOND_EVENT, onRespond);
      window.removeEventListener(COPY_EVENT, onCopy);
    };
  }, []);

  if (loaded.state === "failed" && !shown) {
    return (
      <section className="card">
        <h2>Recommendation</h2>
        <p className="muted">{loaded.message}</p>
        <Link to="/overview">Back to the Overview</Link>
      </section>
    );
  }
  if (!shown) return <p className="muted">Loading the recommendation…</p>;
  const r = shown;
  return (
    <div className="grid">
      <section className="card span-3" aria-label="Recommendation">
        <div className="card-head">
          <h2 data-testid="headline">{headline(r)}</h2>
          <span className="muted small">recommend-only: you place orders on Kalshi</span>
        </div>
        <div className="muted small">
          <Link to={href({ screen: "market", ticker: r.ticker })}>{r.title || r.ticker}</Link> · {ruleName(r)} · {r.state}
          {r.state === "active" && ` · expires ${local(r.expires_utc)}`}
          {r.superseded_by && (
            <>
              {" "}
              by <Link to={href({ screen: "recommendation", id: r.superseded_by })}>a newer one</Link>
            </>
          )}
        </div>
        <div className="stats">
          {figures(r).map((f) => (
            <div key={f.label}>
              <div className="muted small">{f.label}</div>
              <div className={`big ${f.level ?? ""}`}>{f.value}</div>
            </div>
          ))}
        </div>
        <div className="actions">
          {ANSWERS.map((answer) => (
            <button key={answer} type="button" className={r.response === answer ? "primary" : ""} aria-pressed={r.response === answer} onClick={() => void respond(answer)}>
              {LABELS[answer]}
            </button>
          ))}
          <button type="button" onClick={() => void copy()}>
            Copy order
          </button>
          <span className="muted small" role="status" data-testid="note">
            {note}
          </span>
        </div>
      </section>
      <section className="card" aria-label="Why">
        <h2>Why</h2>
        <p>{r.summary}</p>
        <ul className="small">
          {r.inputs.map((input) => (
            <li key={input}>{inputText(input)}</li>
          ))}
        </ul>
        <p className="muted small">Made {local(r.at_utc)}: the time IKARS received what it was built from.</p>
      </section>
      <Guarded title="Checks">
        <Checks r={r} />
      </Guarded>
      <section className="card" aria-label="Provenance">
        <h2>Provenance</h2>
        <table>
          <tbody>
            <tr>
              <td className="muted">Rule</td>
              <td>
                {r.strategy}, registered {r.version}
              </td>
            </tr>
            <tr>
              <td className="muted">Made by</td>
              <td>the {r.source} plugin</td>
            </tr>
            <tr>
              <td className="muted">Inputs</td>
              <td>{r.inputs.length} items</td>
            </tr>
            <tr>
              <td className="muted">LLM</td>
              <td>not used</td>
            </tr>
            <tr>
              <td className="muted">Size</td>
              <td>a fixed {contracts(e2(r.count_e2))} contracts (paper)</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}
