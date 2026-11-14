import unittest
from unittest.mock import patch

from clickhouse_connect.driver.exceptions import OperationalError
from run_databases import query_record


class FakeDatabase:
    def __init__(self, rows=None, error=None) -> None:
        self.rows = rows
        self.error = error
        self.cancelled = False

    def query(self, sql):
        if self.error:
            raise self.error
        return self.rows, {"elapsed_ns": "2000000000", "query_id": "known-id"}

    def cancel(self, marker):
        self.cancelled = True


class RunnerTests(unittest.TestCase):
    config = {"float_relative_tolerance": 1e-9, "float_absolute_tolerance": 1e-6}

    def test_successful_timing_and_separate_server_time(self):
        with patch("run_databases.time.perf_counter", side_effect=[10, 13]):
            record, rows = query_record(FakeDatabase([[42]]), "SELECT 42", [[42]], self.config)
        self.assertEqual(rows, [[42]])
        self.assertEqual(record["elapsed_s"], 3)
        self.assertEqual(record["server_elapsed_s"], 2)
        self.assertEqual(record["validation_status"], "passed")

    def test_mismatch_is_not_success_shaped(self):
        with patch("run_databases.time.perf_counter", side_effect=[10, 13]):
            record, _ = query_record(FakeDatabase([[43]]), "SELECT 43", [[42]], self.config)
        self.assertEqual(record["status"], "validation_failed")
        self.assertIsNone(record["elapsed_s"])
        self.assertEqual(record["observed_elapsed_s"], 3)

    def test_timeout_cancels_and_keeps_failed_record(self):
        db = FakeDatabase(error=OperationalError("Code: 159. TIMEOUT_EXCEEDED"))
        with patch("run_databases.time.perf_counter", side_effect=[10, 70]):
            record, rows = query_record(db, "SELECT 42", [[42]], self.config)
        self.assertTrue(db.cancelled)
        self.assertIsNone(rows)
        self.assertEqual(record["status"], "timeout")
        self.assertIsNone(record["elapsed_s"])
        self.assertEqual(record["observed_elapsed_s"], 60)


if __name__ == "__main__":
    unittest.main()
