// Placeholder until the web terminal is built (M1.8a-web-1b). Trades the one-time code in the
// address's # fragment for the session cookie, removes the code from the address bar, then shows
// the engine's version and a few health facts. Text only: nothing from the engine becomes markup.
"use strict";

async function signIn() {
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  if (!code) return;
  history.replaceState(null, "", location.pathname);
  const answer = await fetch("/v1/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!answer.ok) throw new Error("the sign-in code was used, expired or unknown: run ikars open");
}

function show(name, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = name;
  detail.textContent = String(value);
  document.getElementById("facts").append(term, detail);
}

async function main() {
  const status = document.getElementById("status");
  try {
    await signIn();
    const hello = await fetch("/v1/hello");
    if (hello.status === 401) throw new Error("not signed in: run ikars open");
    if (!hello.ok) throw new Error(`the engine answered ${hello.status}`);
    const facts = await hello.json();
    status.textContent = "Connected to the engine.";
    show("Engine version", facts.engine_version);
    show("API version", facts.api_version);
  } catch (error) {
    status.textContent = error.message;
  }
}

main();
