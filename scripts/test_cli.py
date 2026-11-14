"""Regression tests for the standalone executable; no third-party dependencies."""

from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    executable: Path

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input = self.root / "input.tsv"
        self.input.write_text(
            "row_id\tkey\tvalue\n"
            "3\t9223372036854775807\t9\n"
            "0\t-9223372036854775808\t-4\n"
            "2\t7\t3\n1\t7\t2\n", encoding="ascii",
        )
        self.store = self.root / "store"

    def invoke(self, *args: object, success: bool = True) -> str:
        result = subprocess.run(
            [str(self.executable), *(str(arg) for arg in args)],
            text=True, capture_output=True, timeout=10, check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL:", result.stderr)
        return result.stdout

    def build(self) -> None:
        stats = json.loads(self.invoke("build", self.input, self.store, 2))
        self.assertEqual(set(stats), {"sort_s", "data_write_s", "index_write_s"})
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in stats.values()))

    def test_build_reload_query_output(self) -> None:
        self.build()
        for method in ("full", "index"):
            equality = json.loads(self.invoke("query", self.store, method, "eq", 7, 7, "ids"))
            self.assertEqual(equality["row_ids"], [1, 2])
            self.assertEqual((equality["count"], equality["sum"]), (2, 5))
            aggregate = json.loads(
                self.invoke("query", self.store, method, "range", 7, 8, "aggregate"))
            self.assertNotIn("row_ids", aggregate)
            self.assertEqual((aggregate["count"], aggregate["sum"]), (2, 5))
            for key, row_id in ((-(2**63), 0), (2**63 - 1, 3)):
                result = json.loads(
                    self.invoke("query", self.store, method, "eq", key, key, "ids"))
                self.assertEqual(result["row_ids"], [row_id])
            empty = json.loads(self.invoke("query", self.store, method, "range", 7, 7, "ids"))
            self.assertEqual((empty["count"], empty["sum"], empty["scanned_rows"]), (0, 0, 0))
        self.invoke("build", self.input, self.store, 2, success=False)

    def test_invalid_commands_and_inputs(self) -> None:
        self.build()
        self.invoke("unknown", success=False)
        self.invoke("query", self.store, "invalid", "range", 0, 8, "ids", success=False)
        self.invoke("query", self.store, "index", "range", 8, 0, "ids", success=False)
        self.invoke("query", self.store, "index", "eq", "bad", 0, "ids", success=False)
        self.invoke("query", self.store, "index", "eq", 2**63, 0, "ids", success=False)
        self.invoke("build", self.input, self.root / "zero", 0, success=False)
        self.input.write_text("row_id\tkey\tvalue\n1\t2\t3\n1\t3\t4\n", encoding="ascii")
        self.invoke("build", self.input, self.root / "duplicate", 2, success=False)
        self.input.write_text("row_id\tkey\tvalue\n1\t2\t3\textra\n", encoding="ascii")
        self.invoke("build", self.input, self.root / "bad-row", 2, success=False)
        (self.store / "value.bin").write_bytes(b"X")
        self.invoke("query", self.store, "index", "eq", 7, 7, "ids", success=False)

    def test_benchmark_protocol(self) -> None:
        self.build()
        query_file, log = self.root / "queries.tsv", self.root / "samples.csv"
        query_file.write_text("query_id\tlower\tupper\n1\t6\t8\n2\t7\t8\n"
                              "3\t8\t9\n4\t-10\t10\n", encoding="ascii")
        result = json.loads(self.invoke("benchmark", self.store, query_file, log))
        self.assertEqual(result, {"validated_queries": 4, "samples": 48})
        with log.open(newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(len(rows), 48)
        self.assertEqual(sum(r["phase"] == "measurement" for r in rows), 40)
        for query_id in range(1, 5):
            selected = [r for r in rows if int(r["query_id"]) == query_id]
            self.assertEqual(len(selected), 12)
            for run in range(6):
                pair = [r for r in selected if int(r["run_no"]) == run]
                self.assertEqual([r["method"] for r in pair],
                                 ["full_scan", "sparse_index"] if run % 2 == 0
                                 else ["sparse_index", "full_scan"])
                self.assertEqual(pair[0]["result_count"], pair[1]["result_count"])
                self.assertEqual(pair[0]["result_sum"], pair[1]["result_sum"])
        self.invoke("benchmark", self.store, query_file, log, success=False)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: test_cli.py PATH_TO_MINI_INDEX")
    CliTests.executable = Path(sys.argv[1]).resolve()
    unittest.main(argv=[sys.argv[0]], verbosity=2)
