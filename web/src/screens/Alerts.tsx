import { useValue } from "../app/hooks";
import { alertGroups } from "../view/records";

export function Alerts() {
  const groups = alertGroups(useValue("alerts"));
  return (
    <div className="grid">
      {groups.length === 0 && (
        <section className="card span-3" aria-label="Alerts">
          <h2>Alerts</h2>
          <p className="muted small">No alerts.</p>
        </section>
      )}
      {groups.map((group) => (
        <section className="card span-3" key={group.priority} aria-label={`${group.priority} alerts`}>
          <h2 className={`priority-${group.priority}`}>
            {group.priority} ({group.alerts.length})
          </h2>
          <table>
            <tbody>
              {group.alerts.map((a) => (
                <tr key={a.line}>
                  <td className="nowrap">{a.when}</td>
                  <td>
                    <strong>{a.title}</strong> {a.text}
                  </td>
                  <td className="muted small">{a.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
