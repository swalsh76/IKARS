# Dependencies register

What IKARS needs on a machine: runtimes, libraries, frameworks, drivers and outside services. It is the
source for the install instructions and the release notes (D12). Every slice that adds or drops a dependency updates
this file in the same commit. The "later" entries are what the plan names today; each one is confirmed (or
replaced) in its slice plan, with its licence checked then.

## In use now

### Runtimes and build tools

| What | Version | Where | Licence | Install note |
|---|---|---|---|---|
| Windows 11 | 24H2 or later | target | — | The only supported OS. |
| Python | 3.13 (`.python-version`) | engine | PSF | Developers: managed by `uv`. Releases: bundled standalone CPython (D12); nothing for the user to install. |
| uv | current | development and CI | MIT / Apache-2.0 | `python -m uv` on the workstation; not shipped. |
| gitleaks | current | development and CI | MIT | Needed by the pre-commit hook (`winget install gitleaks`). |
| Node.js | 24.22.1 LTS | development and CI | MIT | Builds the web terminal; not shipped. Per user, no admin: the official `node-v24.22.1-win-x64.zip`, checked against Node's `SHASUMS256.txt`, unpacked to `%LOCALAPPDATA%\Programs\nodejs` and added to the user `PATH` (winget's package is a machine-wide MSI). CI: `actions/setup-node`. |
| pnpm | 12.14.0 (`packageManager` in `web/package.json`) | development and CI | MIT | Through Corepack (`corepack enable`, part of Node 24). Settings in `web/pnpm-workspace.yaml`: a release is used only once 7 days old; install scripts run for none. |

### Python packages (engine)

| Package | Used for | Licence |
|---|---|---|
| `cryptography` | Kalshi request signing (RSA-PSS) | Apache-2.0 / BSD |
| `httpx` | REST client | BSD-3 |
| `pydantic` | Kalshi REST and WebSocket models | MIT |
| `websockets` | the Kalshi market-data feed | BSD-3 |
| `msgspec` | the engine's item model; the loopback API (D3) | BSD-3 |
| `pyarrow` | Arrow IPC and Parquet recordings | Apache-2.0 |
| `tomli-w` | writing the TOML settings | MIT |
| `tzdata` | the time-zone database `zoneinfo` needs on Windows (the tax zone, M1.5a) | Apache-2.0 |
| `starlette` | the loopback API (M1.7, D3) | BSD-3-Clause |
| `uvicorn` | the API's server, inside the engine's event loop (M1.7); with `h11` (MIT), `click` (BSD-3-Clause) | BSD-3-Clause |

Development only: `ruff`, `mypy`, `pytest` (with `pytest-asyncio`, `pytest-cov`, `hypothesis`), `pip-audit`; the
exact set and versions are in `uv.lock`.

### Terminal

The web terminal (`web/`, M1.8a-web-1b). Exact versions in `web/package.json` and `web/pnpm-lock.yaml`; each was at
least 7 days old when chosen (PLAN D11). The .NET WPF terminal and its packages were removed with the D1 amendment.

| Package | Version | Used for | Licence |
|---|---|---|---|
| `react`, `react-dom` | 19.3.1 | the screens (built into the static files; nothing for the user to install) | MIT |
| `ag-grid-community`, `ag-grid-react` | 36.2.0 | the Markets grid (M1.8a-web-2a); its CSS-file theme, so no inline style | MIT |
| `lightweight-charts` | 5.2.1 | the Market screen's price chart (M1.8a-web-2a); its logo is off (it needs an inline style) and the credit with a link to TradingView, which its licence asks for, is drawn below the chart | Apache-2.0 |

Build and test only (not shipped): `vite` 8.3.4 and `@vitejs/plugin-react` 6.1.2 (MIT); `typescript` 6.0.3
(Apache-2.0; typescript-eslint does not support 7 yet); `vitest` and `@vitest/coverage-v8` 5.0.5, `jsdom` 30.1.3 (MIT);
`@playwright/test` 1.64.1 (Apache-2.0; runs the installed Edge, no browser download); `eslint` 10.13.0, `@eslint/js`
10.0.1, `typescript-eslint` 8.72.0, `eslint-plugin-react-hooks` 7.1.1, `globals` 17.13.0 (MIT);
`json-schema-to-typescript` 16.0.0 and `ajv` 8.20.0 (MIT: the generated types and the validators, compiled at build
time); `@types/react`, `@types/react-dom` 19.3.0 and `@types/node` 24.19.2 (MIT). AG Grid and Lightweight Charts are
in use since M1.8a-web-2a (above).

### Outside services

| Service | Used for | Needs |
|---|---|---|
| Kalshi REST and WebSocket (production) | markets, trades, books, exchange status | IKARS's own **read-only** key in `IKARS_KALSHI_KEY_ID` and `IKARS_KALSHI_PRIVATE_KEY_PATH` (D8, D21-3) |
| GitHub (Actions, Releases, Dependabot) | CI and, later, downloads | nothing on the user's machine |
| GitHub CodeQL (`github/codeql-action`, MIT) | static security analysis in CI (2026-10-27) | nothing on the user's machine |

## Needed later (planned)

| What | Slice | Base or optional | Licence | Install note |
|---|---|---|---|---|
| **GPU detection** | M1.6 | base | — | Reads the NVIDIA driver through `nvidia-smi` (ships with the driver) or NVML (`nvidia-ml-py`, BSD); chosen in M1.6's plan. No GPU means CPU paths only, reported in health. |
| Windows Task Scheduler | M1.6 | base | built in | A per-user logon task and a watchdog task; no admin. |
| Windows notifications (toasts) | M1.6 | base | built in | Through Windows PowerShell's WinRT notification classes; no package. (Phone pushes with ntfy were dropped by the owner, 2026-10-27.) |
| DPAPI | M1.5 / M1.6 | base | built in | Through the standard library (`ctypes`) or `pywin32`; chosen in the slice. |
| DuckDB | M1.5 onward (queries, D4) | base | MIT | Python wheel. |
| PDF/A writer for the tax package | M5 (D18) | base | to choose | Library chosen in M5's plan. |
| Claude API (`anthropic` SDK) | M2 (D9) | base | MIT | An API key in an environment variable or DPAPI; a monthly cost ceiling (D21-5). |
| NWS / NOAA `api.weather.gov` | M2 (D5 B) | base | public service | No key; a `User-Agent` with contact details is required. |
| RSS / Atom parser (for example `feedparser`) and GDELT | M2 (D5 C) | base | BSD-2 (feedparser); GDELT terms per source | Chosen in M2's plan; per-source terms reviewed (D19 compliance). |
| **NVIDIA driver with CUDA 12.8 or later** | M2 onward (D15) | optional | proprietary | Blackwell (`sm_120`) needs CUDA 12.8+. The user installs the driver; IKARS never does. |
| ONNX Runtime (CUDA) and sentence-transformers models | M2 (D15 embeddings) | optional extra | MIT; model licences per model | Downloaded on demand from the terminal's settings, never bundled (D12). |
| Local LLM runtime (llama.cpp server, Ollama or ONNX Runtime GenAI) and a 7 to 14B quantised model | M2 (D9) | optional extra | MIT (runtimes); model licences per model | Chosen by measured speed and quality in the slice; downloaded on demand. |
| XGBoost (CUDA) or LightGBM; PyTorch (cu128) for sequence models; CuPy for sweeps | M3 to M5 (D15, D16) | optional extra | Apache-2.0; MIT; BSD-3; MIT | CUDA wheels run to gigabytes: optional, on demand. CPU fallbacks for each. |
| Other-venue prices (exchange WebSockets, odds APIs) | later milestones (D19 G7) | plugin | per source | A paid odds API counts against the cost ceiling. |
| **Microsoft Edge** | in use since M1.8a-web-1a (`ikars open`) | base | built into Windows 11 | Shows the terminal as an app window with its own IKARS profile under `%LOCALAPPDATA%\IKARS\browser`. |
| Velopack | M1.9 (D12) | build only | MIT | Builds `Setup.exe` and the portable zip; the user installs nothing extra. |
| Code signing (SignPath free plan or Azure Trusted Signing) | M1.9 (D12) | build only | service | Removes SmartScreen warnings; chosen in the packaging slice. |
