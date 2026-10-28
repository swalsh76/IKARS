# IKARS

IKARS watches external news and data sources, Kalshi's live markets and order books, and turns them into **trade
recommendations in a real-time Windows 11 terminal**. Every recommendation shows its profit or loss **after fees and
other entry and exit costs**. IKARS is aimed at prices set by people trading at a human pace, not at the
millisecond races professional firms win.

It is a Python engine with a TypeScript web terminal, served by the engine on the workstation's loopback address
and shown in an Edge app window. Its infrastructure comes from the owner's earlier projects KAPS, SPARQ and
KalTerm3.

**Status:** being set up (milestone M1). IKARS only recommends; it sends no orders. See [docs/PLAN.md](docs/PLAN.md)
for the plan and [docs/BUILD-LOG.md](docs/BUILD-LOG.md) for how each part was built and verified.

**Downloads:** test pre-releases on the [Releases](https://github.com/swalsh76/IKARS/releases) page (`Setup.exe`
installs per user, without admin rights, and updates itself; or the portable zip). See the
[code signing policy](docs/CODE-SIGNING.md).

IKARS is a personal project and is **not affiliated with, endorsed by or connected to Kalshi**. Nothing here is
financial or tax advice.

MIT licensed.
