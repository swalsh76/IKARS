from data_guard import TEST_FILE_LIMIT_BYTES, problems


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
