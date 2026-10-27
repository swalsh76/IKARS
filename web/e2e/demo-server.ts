// Starts the engine's API server on made-up data (python -m ikars_engine demo-api) for the screen
// tests, and stops it after them. Its first line says where it listens and its throw-away token.

import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { join } from "node:path";
import { createInterface } from "node:readline";

export interface Demo {
  port: number;
  token: string;
  stop(): void;
}

/** A demo server with these extra arguments (``--first-run``: no settings, so the wizard opens). */
export async function startDemo(extra: string[] = []): Promise<Demo> {
  const repository = join(import.meta.dirname, "..", "..");
  // uv as this workstation runs it (python -m uv), or as CI installs it (IKARS_UV=uv)
  const [uv, ...uvArgs] = (process.env.IKARS_UV ?? "python -m uv").split(" ");
  const args = ["--markets", process.env.IKARS_DEMO_MARKETS ?? "30", "--hz", process.env.IKARS_DEMO_HZ ?? "5", ...extra];
  const server: ChildProcess = spawn(uv ?? "python", [...uvArgs, "run", "python", "-m", "ikars_engine", "demo-api", ...args], {
    cwd: repository,
    stdio: ["ignore", "pipe", "inherit"],
  });
  const first = await new Promise<string>((resolve, reject) => {
    if (!server.stdout) return reject(new Error("demo-api has no output"));
    const lines = createInterface({ input: server.stdout });
    lines.once("line", resolve);
    server.once("exit", (code) => reject(new Error(`demo-api ended (${String(code)}) before it started`)));
    setTimeout(() => reject(new Error("demo-api did not start in 60 s")), 60_000);
  });
  const started = JSON.parse(first) as { port: number; token: string };
  return {
    port: started.port,
    token: started.token,
    stop() {
      // uv starts the server as a child: on Windows only the whole tree stops it (kill() left it running)
      if (process.platform === "win32" && server.pid !== undefined) {
        spawnSync("taskkill", ["/PID", String(server.pid), "/T", "/F"], { stdio: "ignore" });
      } else {
        server.kill();
      }
    },
  };
}

export default async function demoServer(): Promise<() => Promise<void>> {
  const demo = await startDemo();
  process.env.IKARS_DEMO_PORT = String(demo.port);
  process.env.IKARS_DEMO_TOKEN = demo.token;
  return async () => demo.stop();
}
