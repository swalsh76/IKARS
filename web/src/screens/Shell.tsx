import { useState, type MouseEvent, type ReactNode } from "react";
import { useRoute, useTerminal, useValue } from "../app/hooks";
import { openNewWindow } from "../app/layout";
import { crumbs, href, type Route } from "../nav/route";
import { strip } from "../view/health";
import { Hotkeys } from "./Keys";

/** An in-app link: the navigator moves, the page does not reload (a modified click opens as usual). */
export function Link({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  const { navigator } = useTerminal();
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigator.go(to);
  };
  return (
    <a href={to} onClick={follow} className={className}>
      {children}
    </a>
  );
}

const NAV: { label: string; to: string; screens: Route["screen"][] }[] = [
  { label: "Overview", to: "/overview", screens: ["overview"] },
  { label: "Markets", to: "/markets", screens: ["markets", "market"] },
  { label: "Ledger", to: "/ledger", screens: ["ledger"] },
  { label: "Alerts", to: "/alerts", screens: ["alerts"] },
  { label: "Health", to: "/health", screens: ["health"] },
  { label: "Settings", to: "/settings", screens: ["settings", "welcome"] },
];

function Search() {
  const { navigator } = useTerminal();
  const [query, setQuery] = useState("");
  return (
    <form
      className="search"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        navigator.go(href({ screen: "markets", query: query.trim() }));
      }}
    >
      <label htmlFor="search" className="hidden">
        Search markets
      </label>
      <input id="search" type="search" placeholder="Search markets" value={query} onChange={(e) => setQuery(e.target.value)} />
    </form>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const route = useRoute();
  const health = useValue("health");
  const connection = useValue("connection");
  const items = strip(health, connection);
  const pill = items[0];
  const trail = crumbs(route);
  return (
    <div className="shell">
      <header className="top">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M3 17l5-6 4 4 8-10" />
            <path d="M15 5h5v5" />
          </svg>
          <strong>IKARS</strong>
        </div>
        <nav aria-label="Breadcrumb" className="crumbs">
          {trail.map((crumb, i) => (
            <span key={`${crumb.label}-${i}`}>
              {i > 0 && <span aria-hidden="true"> › </span>}
              {crumb.href ? <Link to={crumb.href}>{crumb.label}</Link> : <span aria-current="page">{crumb.label}</span>}
            </span>
          ))}
        </nav>
        <Search />
        {pill && (
          <Link to="/health" className={`pill level-${pill.level}`}>
            <span className="dot" aria-hidden="true" />
            {pill.label}
          </Link>
        )}
      </header>
      <div className="body">
        <nav aria-label="Main" className="side">
          {NAV.map((item) => (
            <Link key={item.to} to={item.to} className={item.screens.includes(route.screen) ? "on" : ""}>
              {item.label}
            </Link>
          ))}
          <button type="button" className="side-new" onClick={() => openNewWindow()}>
            + New window
          </button>
        </nav>
        <main className="main">{children}</main>
      </div>
      <Hotkeys />
      <footer className="strip" aria-label="Engine status">
        {items.map((item) => (
          <span key={item.label} className={`level-${item.level}`}>
            {item.label}
          </span>
        ))}
      </footer>
    </div>
  );
}

/** A panel whose data arrives in a later slice: drawn in its place now, so nothing moves later. */
export function Later({ title, slice, note }: { title: string; slice: string; note?: string }) {
  return (
    <section className="card later" aria-label={title}>
      <h2>{title}</h2>
      <p className="muted">Arrives in {slice}.</p>
      {note && <p className="muted small">{note}</p>}
    </section>
  );
}
