# Code signing policy

IKARS's Windows downloads (`IKARS-App-win-Setup.exe`, the portable zip and the programs inside them) are built only
by this repository's GitHub Actions release workflow ([`.github/workflows/release.yml`](../.github/workflows/release.yml))
from a version tag. Nothing built anywhere else is released.

**Status:** the downloads are **not signed yet**. An application to the [SignPath Foundation](https://signpath.org)'s
free code signing for open-source projects is pending. Once it is approved, this line becomes:

> Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
> [SignPath Foundation](https://signpath.org).

Until then, Windows SmartScreen shows an "unknown publisher" warning for the installer. The releases are
pre-releases meant for the owner.

## Team roles

| Role | Who |
|---|---|
| Committers and reviewers | [Steven Walsh](https://github.com/swalsh76) (owner) |
| Approvers (each signing request) | [Steven Walsh](https://github.com/swalsh76) |

Every change reaches `main` through a pull request that has to pass the required checks: the Python, web and
hygiene jobs, secret scanning and CodeQL. A release is a tag that the owner pushes.

## Privacy policy

This program will not transfer any information to other networked systems unless specifically requested by the
user or the person installing or operating it. In operation, IKARS connects to:

- **Kalshi's API**, with the user's own key, to read markets and the user's own account. IKARS sends no orders.
- **GitHub Releases**, to check for updates (the request carries no user data).
- **A backup share**, only if the user sets one up.

IKARS keeps its data in the user's own profile. It never sends logs or support bundles anywhere.
