import { Suspense, lazy, useEffect } from "react";
import { useRoute } from "../app/hooks";
import { title, type Route } from "../nav/route";
import { Guarded } from "./Guarded";
import { Alerts } from "./Alerts";
import { Health } from "./Health";
import { Ledger } from "./Ledger";
import { Settings } from "./Settings";
import { Welcome } from "./Welcome";
import { Overview } from "./Overview";
import { Link, Shell } from "./Shell";

// the grid and chart libraries load only when their screens first open (a lighter start)
const Markets = lazy(() => import("./Markets").then((m) => ({ default: m.Markets })));
const Market = lazy(() => import("./Market").then((m) => ({ default: m.Market })));

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
      return <Markets query={route.query} category={route.category} />;
    case "market":
      return <Market ticker={route.ticker} />;
    case "ledger":
      return <Ledger />;
    case "alerts":
      return <Alerts />;
    case "settings":
      return <Settings />;
    case "welcome":
      return <Welcome />;
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

/** The screen a route shows: a search typed on the Markets screen keeps the same screen. */
function screenKey(route: Route): string {
  return route.screen === "market" ? `market:${route.ticker}` : route.screen;
}

export function App() {
  const route = useRoute();
  return (
    <Shell>
      {/* keyed by the screen: one that failed does not keep the next from drawing, and typing a
          search (which changes the address) does not redraw the screen from scratch */}
      <Guarded title="This screen" key={screenKey(route)}>
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <Screen />
        </Suspense>
      </Guarded>
    </Shell>
  );
}
