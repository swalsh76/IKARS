from data_guard import TEST_FILE_LIMIT_BYTES, named_machines, problems


def test_data_and_key_files_are_refused_anywhere() -> None:
    paths = [
        "data/a.parquet",
        "x/b.DUCKDB",
        "x/b.duckdb.wal",
        "c.sqlite",
        "c.sqlite3",
        "c.sqlite-wal",
        "ikars.db-wal",
        "ikars.db-journal",
        "k.pem",
        "k.p12",
        "y/z.jsonl",
    ]
    assert len(problems(paths, {})) == len(paths)


def test_ledger_exports_are_refused() -> None:
    paths = ["IKARS-2026-summary.pdf", "out/IKARS-2026-form8949.csv", "IKARS-2026-manifest.txt"]
    assert len(problems(paths, {})) == len(paths)


def test_ordinary_csv_pdf_and_txt_pass() -> None:
    paths = [
        "docs/diagram.pdf",
        "tests/fixtures/prices.csv",
        "notes.txt",
        "ikars-notes.csv",
        "packages/ikars-engine/README.txt",
        "ikars-engine.txt",
    ]
    assert problems(paths, {}) == []


def test_ordinary_files_pass() -> None:
    assert problems(["README.md", "packages/p/tests/fixtures/small.json"], {}) == []


def test_large_files_are_refused_only_under_tests() -> None:
    big = TEST_FILE_LIMIT_BYTES + 1
    sizes = {"packages/p/tests/big.json": big, "docs/big.md": big}
    assert problems(list(sizes), sizes) == [
        f"packages/p/tests/big.json: over {TEST_FILE_LIMIT_BYTES:,} bytes in a tests folder"
    ]


def test_web_output_and_env_files_are_refused() -> None:
    """M1.8a-web-1b: Playwright's reports and traces, recordings and settings files can hold live
    data or secrets."""
    paths = [
        "web/playwright-report/index.html",
        "web/test-results/run/trace.zip",
        "web/blob-report/a.json",
        "web/node_modules/react/index.js",
        "web/coverage/index.html",
        "web/dist/assets/index.js",
        "web/.env",
        "web/.env.local",
        "web/e2e/session.har",
    ]
    assert len(problems(paths, {})) == len(paths)


def test_the_web_terminal_s_sources_pass() -> None:
    paths = [
        "web/src/main.tsx",
        "web/src/environment.ts",
        "web/package.json",
        "web/pnpm-lock.yaml",
        "web/e2e/terminal.spec.ts",
        "web/src/api/validators.js",
    ]
    assert problems(paths, {}) == []


def test_local_settings_archives_logs_and_output_folders_are_refused() -> None:
    """M1.R3 E10: the guard went by data extensions only."""
    refused = [
        "settings.toml",
        "packages/x/tests/fixtures/settings.toml",
        ".claude/settings.local.json",
        "support/ikars-support-20261102.zip",
        "release/IKARS-App-0.1.18-full.nupkg",
        "notes/engine.log",
        "IKARS-test.wsb",
        "build/IKARS-App/python/x.py",
        "data/journal.txt",
        "logs/engine.txt",
        "backups/pre-upgrade/x.txt",
    ]
    assert len(problems(refused, {})) == len(refused)
    assert problems(["docs/settings.md", ".claude/agents/x.md"], {}) == []


def test_a_machine_s_own_names_are_refused_and_placeholders_pass() -> None:
    """M1.R3 E10: a UNC host or a user profile's name must be a placeholder."""
    bs = "\\"  # the paths are built, so this file itself names no machine (the guard reads it)
    real = [
        ("docs/x.md", f"net use {bs * 2}kal1{bs}share"),
        ("a.py", f'SHARE = r"{bs * 2}fileserver{bs}ikars"'),
        ("b.md", f"see `C:{bs}Users{bs}bob{bs}keys`"),
        ("c.py", f'"C:{bs * 2}Users{bs * 2}bob{bs * 2}keys"'),
    ]
    assert len(named_machines(real)) == len(real)
    placeholders = [
        ("docs/x.md", r"`\\<nas>\<share>`"),
        ("a.py", r'SHARE = r"\\made-up-host\made-up-share"'),
        ("b.py", r"tests use `\\127.0.0.1\ikars-no-such-share`"),
        ("c.py", r'KEY_PATH = r"C:\Users\someone\.ssh\x.pem"'),
        ("d.py", r'"-----BEGIN-----\\nabc\\n-----END-----"'),  # an escaped newline, no path
        ("e.py", r"HKCU\\Software\\Microsoft"),  # a registry key
    ]
    assert named_machines(placeholders) == []
