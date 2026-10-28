"""Build IKARS's two downloads (M1.9a, PLAN D12): ``Setup.exe`` and a portable zip, by Velopack.

    python tools/release/build.py --version 0.1.0

(``web/dist`` must be built first: ``pnpm build`` in ``web/``.) Everything is written under
``build/``:

- ``build/IKARS-App/``: the program folder Velopack packs:
  - ``IKARS.exe``: the launcher (``tools/launcher``)
  - ``python/``: a standalone CPython (uv's managed build of ``.python-version``), with the locked
    packages and IKARS's own installed into it; nothing frozen; no tests, IDLE or Tk
  - ``web/dist/``: the built terminal, where the engine looks for it (``webserve.terminal_root``)
- ``build/releases/``: ``IKARS-App-win-Setup.exe``, ``IKARS-App-win-Portable.zip`` and Velopack's
  update packages.
- ``build/IKARS-test.wsb``: Windows Sandbox for the clean-machine checks
  (``tools/release/sandbox``); it names this machine's folders, so it is written here, never
  committed.

The program id ``IKARS-App`` is its install folder (``%LOCALAPPDATA%\\IKARS-App``), apart from the
data folder (``%LOCALAPPDATA%\\IKARS``), which Velopack never touches (BUILD-LOG 1.9).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
APP = BUILD / "IKARS-App"
RELEASES = BUILD / "releases"
PACK_ID = "IKARS-App"
# left out of the bundled Python: its own tests, IDLE, Tk and the pip bootstrap (about 40 MB)
LEFT_OUT = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "tcl", "__pycache__"}
SEMVER = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")


def uv() -> list[str]:
    """uv as this machine runs it (CI: ``IKARS_UV=uv``; the workstation: ``python -m uv``)."""
    named = os.environ.get("IKARS_UV")
    return [named] if named else [sys.executable, "-m", "uv"]


def run(*command: str | Path, cwd: Path = ROOT) -> str:
    print(">", " ".join(str(c) for c in command), flush=True)
    done = subprocess.run([str(c) for c in command], cwd=cwd, check=True,  # noqa: S603
                          capture_output=True, text=True)  # fmt: skip
    return done.stdout


def python_home() -> Path:
    wanted = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    # --system: never the project's .venv (uv prefers it otherwise, and a venv is no standalone)
    found = run(*uv(), "python", "find", "--system", "--python-preference", "only-managed", wanted)
    home = Path(found.strip()).parent
    if (home / "pyvenv.cfg").exists() or not (home / "Lib" / "os.py").is_file():
        raise SystemExit(f"{home} is not a standalone Python: run uv python install {wanted}")
    return home


def left_out(folder: str, names: list[str]) -> set[str]:
    return {n for n in names if n in LEFT_OUT}


def build(version: str) -> None:
    if not SEMVER.fullmatch(version):
        raise SystemExit(f"{version!r} is not a version like 1.2.3 or 1.2.3-beta.1")
    web = ROOT / "web" / "dist"
    if not (web / "index.html").is_file():
        raise SystemExit("web/dist is not built: run pnpm build in web/ first")
    for folder in (APP, BUILD / "wheels"):
        if folder.exists():
            shutil.rmtree(folder)
    # emptied, not removed: an open Windows Sandbox holds the folder itself (it is mapped there)
    RELEASES.mkdir(parents=True, exist_ok=True)
    for old in RELEASES.iterdir():
        shutil.rmtree(old) if old.is_dir() else old.unlink()
    python = APP / "python"
    shutil.copytree(python_home(), python, ignore=left_out)
    exe = python / "python.exe"

    # the locked packages (hashes required), then IKARS's own two as wheels
    requirements = BUILD / "requirements.txt"
    run(*uv(), "export", "--frozen", "--package", "ikars-engine", "--no-dev",
        "--no-emit-workspace", "--format", "requirements-txt", "-o", requirements)  # fmt: skip
    install = [*uv(), "pip", "install", "--python", str(exe), "--break-system-packages"]
    run(*install, "--require-hashes", "-r", requirements)
    for package in ("ikars-core", "ikars-engine"):
        run(*uv(), "build", "--package", package, "--wheel", "--out-dir", BUILD / "wheels")
    run(*install, "--no-deps", *sorted(str(w) for w in (BUILD / "wheels").glob("*.whl")))
    for script in (python / "Scripts").glob("*.exe"):
        script.unlink()  # pip's launchers name this build folder's Python: useless once installed
    run(exe, "-m", "compileall", "-q", "-j", "0", python / "Lib")  # a quicker first start

    shutil.copytree(web, APP / "web" / "dist")
    run("dotnet", "build", ROOT / "tools" / "launcher", "-c", "Release", "-nologo",
        "-o", BUILD / "launcher", f"-p:Version={version.split('-')[0]}")  # fmt: skip
    for name in ("IKARS.exe", "IKARS.exe.config"):
        shutil.copy2(BUILD / "launcher" / name, APP / name)
    shutil.copy2(ROOT / "LICENSE", APP / "LICENSE.txt")

    run("dotnet", "tool", "restore")
    run("dotnet", "vpk", "pack", "--packId", PACK_ID, "--packVersion", version,
        "--packDir", APP, "--mainExe", "IKARS.exe", "--packTitle", "IKARS",
        "--packAuthors", "IKARS", "--shortcuts", "StartMenuRoot", "--outputDir", RELEASES,
        # IKARS.exe passes Velopack's steps to Python, which answers them (ikars_engine.launcher)
        "--skipVeloAppCheck")  # fmt: skip
    for made in sorted(RELEASES.iterdir()):
        print(f"{made.name}: {made.stat().st_size / 1e6:.1f} MB")
    sandbox()


def sandbox() -> Path:
    """Windows Sandbox with the releases and the checks mapped read-only; it opens PowerShell on the
    first check (open the file to start it)."""
    checks = ROOT / "tools" / "release" / "sandbox"
    start = r"powershell -NoExit -ExecutionPolicy Bypass -File C:\IKARS-checks\check.ps1 install"
    config = BUILD / "IKARS-test.wsb"
    mapped = [(RELEASES, r"C:\IKARS-releases"), (checks, r"C:\IKARS-checks")]
    folders = "".join(
        f"    <MappedFolder><HostFolder>{host}</HostFolder><SandboxFolder>{inside}</SandboxFolder>"
        "<ReadOnly>true</ReadOnly></MappedFolder>\n"
        for host, inside in mapped
    )
    config.write_text(
        "<Configuration>\n  <MemoryInMB>4096</MemoryInMB>\n  <MappedFolders>\n"
        + folders
        + "  </MappedFolders>\n"
        f"  <LogonCommand><Command>cmd /c start {start}</Command></LogonCommand>\n"
        "</Configuration>\n",
        encoding="utf-8",
    )
    print(f"Windows Sandbox: open {config}")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="the release's version, like 0.1.0")
    build(parser.parse_args().version)


if __name__ == "__main__":
    main()
