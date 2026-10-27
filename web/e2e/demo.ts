// The demo server the screen tests run against (started by demo-server.ts), shared by every spec.

/** The demo server's address. */
export const base = (): string => `http://127.0.0.1:${process.env.IKARS_DEMO_PORT ?? "0"}`;

/** A fresh one-time sign-in address, as ikars open gets it (the bearer token is demo-api's own). */
export async function signInUrl(): Promise<string> {
  const answer = await fetch(`${base()}/v1/session/code`, {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.IKARS_DEMO_TOKEN ?? ""}` },
  });
  if (!answer.ok) throw new Error(`the demo server refused a sign-in code: ${answer.status}`);
  return ((await answer.json()) as { url: string }).url;
}
