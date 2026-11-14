import copy
import tempfile
import unittest
from pathlib import Path

from run_mechanism import (
    config_from, frozen_queries, prepare_data, sha256, summarize, validate_samples,
)


class MechanismTests(unittest.TestCase):
    def test_frozen_intervals(self) -> None:
        for n in (100000, 1000000):
            queries = frozen_queries(n, ["0.001", "0.01", "0.1", "1"])
            self.assertEqual(queries[-1]["lower_bound"], 0)
            self.assertEqual(queries[-1]["upper_bound"], n // 4)
            self.assertEqual(queries[0]["upper_bound"] - queries[0]["lower_bound"], n // 4000)

    def test_generator_reproducibility_and_oracle(self) -> None:
        config = config_from(Path(__file__).resolve().parents[1] / "configs" / "mechanism.json")
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary) / "a.tsv", Path(temporary) / "b.tsv"
            queries = prepare_data(first, 1000, config)
            self.assertEqual(queries, prepare_data(second, 1000, config))
            self.assertEqual(sha256(first), sha256(second))
            for query in queries:
                selected = []
                for line in first.read_text().splitlines()[1:]:
                    _, key, value = map(int, line.split())
                    if query["lower_bound"] <= key < query["upper_bound"]:
                        selected.append(value)
                self.assertEqual(query["expected_count"], len(selected))
                self.assertEqual(query["expected_sum"], sum(selected))

    def test_sample_shape_and_failures(self) -> None:
        queries = frozen_queries(100000, ["0.001", "0.01", "0.1", "1"])
        rows = []
        for query in queries:
            for method in ("full_scan", "sparse_index"):
                for run in range(6):
                    rows.append({
                        "query_id": str(query["query_id"]), "method": method, "run_no": str(run),
                        "phase": "warmup" if run == 0 else "measurement",
                        "elapsed_s": "0.01", "validation_status": "passed",
                        "lower_bound": str(query["lower_bound"]),
                        "upper_bound": str(query["upper_bound"]), "result_count": "0",
                        "result_sum": "0", "matched_rows": "0", "scanned_rows": "100000",
                        "requested_read_bytes": "2400000", "index_bytes": "4704",
                    })
        validate_samples(rows, 100000, 512, queries)
        for field, value in (("elapsed_s", "nan"), ("result_sum", "1"),
                             ("phase", "measurement"), ("lower_bound", "-1"),
                             ("validation_status", "failed"), ("index_bytes", "1")):
            broken = copy.deepcopy(rows)
            broken[0][field] = value
            with self.assertRaises(ValueError, msg=field):
                validate_samples(broken, 100000, 512, queries)
        with self.assertRaises(ValueError):
            validate_samples(rows[:-1], 100000, 512, queries)
        broken = copy.deepcopy(rows)
        broken[0] = broken[1]
        with self.assertRaises(ValueError):
            validate_samples(broken, 100000, 512, queries)

    def test_summary_requires_complete_measurements(self) -> None:
        records = []
        for n in (100000, 1000000):
            for granule in (512, 2048, 8192):
                for query_id in range(1, 5):
                    for method in ("full_scan", "sparse_index"):
                        for run in range(1, 6):
                            records.append({
                                "dataset_rows": n, "granule_rows": granule,
                                "query_id": query_id, "method": method,
                                "phase": "measurement", "run_no": run,
                                "elapsed_s": run * (2 if method == "full_scan" else 1),
                                "validation_status": "passed", "target_match_ratio": "0.1",
                                "actual_match_ratio": 0.1, "scanned_rows": n,
                                "index_bytes": ((n + granule - 1) // granule) * 24,
                            })
        summary = summarize(records)
        self.assertEqual(len(summary), 24)
        self.assertEqual(summary[0]["full_scan_median_s"], 6)
        self.assertEqual(summary[0]["sparse_index_min_s"], 1)
        self.assertEqual(summary[0]["sparse_index_max_s"], 5)
        self.assertEqual(summary[0]["speedup"], 2)
        with self.assertRaises(ValueError):
            summarize(records[:-1])
        broken = copy.deepcopy(records)
        broken[-1]["validation_status"] = "failed"
        with self.assertRaises(ValueError):
            summarize(broken)


if __name__ == "__main__":
    unittest.main()
