from data_guard import TEST_FILE_LIMIT_BYTES, problems


def test_data_and_key_files_are_refused_anywhere() -> None:
    paths = ["data/a.parquet", "x/b.DUCKDB", "c.sqlite", "c.sqlite-wal", "k.pem", "y/z.jsonl"]
    assert len(problems(paths, {})) == len(paths)


def test_ordinary_files_pass() -> None:
    assert problems(["README.md", "packages/p/tests/fixtures/small.json"], {}) == []


def test_large_files_are_refused_only_under_tests() -> None:
    big = TEST_FILE_LIMIT_BYTES + 1
    sizes = {"packages/p/tests/big.json": big, "docs/big.md": big}
    assert problems(list(sizes), sizes) == [
        f"packages/p/tests/big.json: over {TEST_FILE_LIMIT_BYTES:,} bytes in a tests folder"
    ]
