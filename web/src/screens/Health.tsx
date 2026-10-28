import { useState } from "react";
import { ApiFailure, change } from "../api/client";
import { useMeterReading, useSnapshot, useValue } from "../app/hooks";
import { gapRow, healthCards, meterCard } from "../view/health";

function Gaps() {
  const gaps = useSnapshot("/v1/gaps?days=7&limit=200", "GapPage", 30_000);
  return (
    <section className="card span-3" aria-labelledby="gaps">
      <h2 id="gaps">Gap log (last 7 days)</h2>
      {gaps.state === "loading" && <p className="muted small">Loading…</p>}
      {gaps.state === "failed" && <p className="muted small">{gaps.message}</p>}
      {gaps.state === "ready" && gaps.stale && <p className="muted small">Not refreshed: {gaps.stale}</p>}
      {gaps.state === "ready" && gaps.value.items.length === 0 && <p className="muted small">No gaps.</p>}
      {gaps.state === "ready" && gaps.value.items.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Length</th>
                <th>Recovered</th>
              </tr>
            </thead>
            <tbody>
              {gaps.value.items.map((gap) => {
                const row = gapRow(gap);
                return (
                  <tr key={gap.id}>
                    <td>{row.when}</td>
                    <td>{row.what}</td>
                    <td>{row.length}</td>
                    <td>{row.recovered}</td>
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

const BUNDLE_WAIT_MS = 120_000; // up to 50 MB of logs are redacted and checked

/** A redacted support bundle, saved in IKARS's support folder (D20 I; M1.8b-2). */
function SupportBundle() {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const make = () => {
    setBusy(true); // one at a time (the engine refuses a second anyway)
    setNote("Making the bundle…");
    change("/v1/support/bundle", "Done", {}, { method: "POST", timeoutMs: BUNDLE_WAIT_MS })
      .then((done) => setNote(done.detail))
      .catch((error: unknown) => setNote(error instanceof ApiFailure ? error.message : "could not make the bundle"))
      .finally(() => setBusy(false));
  };
  return (
    <section className="card span-3" aria-label="Support bundle">
      <h2>Support bundle</h2>
      <p className="muted small">
        The logs, this report, the versions, the settings and the gap log in one zip in IKARS&apos;s support folder. Keys, tokens, paths and account amounts are taken out and
        checked for before it is kept. IKARS never sends it anywhere.
      </p>
      <div className="actions">
        <button type="button" onClick={make} disabled={busy}>
          Make a support bundle
        </button>
        <span className="muted small" data-testid="support-note">
          {note}
        </span>
      </div>
    </section>
  );
}

export function Health() {
  const health = useValue("health");
  const reading = useMeterReading();
  if (health === null) return <p className="muted">Waiting for the engine's health…</p>;
  return (
    <div className="grid">
      {[...healthCards(health), meterCard(reading)].map((card) => (
        <section className="card" key={card.title} aria-label={card.title}>
          <div className="card-head">
            <span className={`dot level-${card.level}`} aria-hidden="true" />
            <h2>{card.title}</h2>
            <span className={`state level-${card.level}`}>{card.state}</span>
          </div>
          <div className="big">{card.big}</div>
          {card.lines.map((line) => (
            <div className="sub small" key={line}>
              {line}
            </div>
          ))}
        </section>
      ))}
      <Gaps />
      <SupportBundle />
      <section className="card span-3">
        <details>
          <summary>The engine's whole report</summary>
          <pre>{JSON.stringify(health.detail, null, 2)}</pre>
        </details>
      </section>
    </div>
  );
}
