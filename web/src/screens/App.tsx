import { useEffect } from "react";
import { useRoute } from "../app/hooks";
import { title } from "../nav/route";
import { Health } from "./Health";
import { Overview } from "./Overview";
import { Later, Link, Shell } from "./Shell";

function Screen() {
  const route = useRoute();
  useEffect(() => {
    document.title = title(route);
  }, [route]);
  switch (route.screen) {
    case "overview":
      return <Overview />;
    case "health":
      return <Health />;
    case "markets":
    case "market":
      return <Later title="Markets" slice="M1.8a-web-2a" />;
    case "ledger":
    case "alerts":
      return <Later title={route.screen === "ledger" ? "Ledger" : "Alerts"} slice="M1.8a-web-2b" />;
    case "missing":
      return (
        <section className="card">
          <h2>Not found</h2>
          <p className="muted">IKARS has no screen at this address.</p>
          <Link to="/overview">Back to the Overview</Link>
        </section>
      );
  }
}

export function App() {
  return (
    <Shell>
      <Screen />
    </Shell>
  );
}
