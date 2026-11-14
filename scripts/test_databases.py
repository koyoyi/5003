import csv
import io
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal

from database_support import (
    Fingerprint, adapt_query, adapt_schema, clickhouse_insert_rows, equal_results, normalized,
    preserve_license,
)


class DatabaseTests(unittest.TestCase):
    def test_deterministic_ties(self) -> None:
        original = "SELECT UserID, COUNT(*) FROM hits GROUP BY UserID ORDER BY COUNT(*) DESC LIMIT 10;"
        for dbms in ("clickhouse", "postgresql", "mysql"):
            sql, reasons = adapt_query(dbms, 16, original)
            self.assertIn("ORDER BY COUNT(*) DESC, UserID LIMIT 10", sql)
            self.assertTrue(reasons)
        sql, _ = adapt_query("mysql", 18, "SELECT UserID, SearchPhrase, COUNT(*) FROM hits "
                            "GROUP BY UserID, SearchPhrase LIMIT 10;")
        self.assertIn("ORDER BY UserID, SearchPhrase LIMIT 10", sql)
        sql, _ = adapt_query("clickhouse", 23,
                            "SELECT SearchPhrase, COUNT(*) AS c FROM hits "
                            "GROUP BY SearchPhrase ORDER BY c DESC LIMIT 10;")
        self.assertIn("ORDER BY c DESC, SearchPhrase LIMIT 10", sql)

    def test_dialect_time_length_regex(self) -> None:
        sql, _ = adapt_query("mysql", 43,
                            "SELECT DATE_FORMAT(EventTime, '%Y-%m-%d %H:00:00') FROM hits LIMIT 10;")
        self.assertIn("%H:%i:00", sql)
        original = r"SELECT REGEXP_REPLACE(Referer, '^https?://(?:www\.)?([^/]+)/.*$', '\1'), " \
                   "AVG(length(Referer)) FROM hits ORDER BY l DESC LIMIT 25;"
        ch, _ = adapt_query("clickhouse", 29, original)
        mysql, _ = adapt_query("mysql", 29, original)
        self.assertIn("replaceRegexpAll", ch)
        self.assertIn(r"'\\1'", ch)
        self.assertIn(r"www\\.", ch)
        self.assertIn("lengthUTF8", ch)
        self.assertIn("'$1'", mysql)
        self.assertIn("CHAR_LENGTH", mysql)
        self.assertIn("(?s)", ch)
        self.assertIn("(?s)", mysql)
        ch, _ = adapt_query("clickhouse", 4, "SELECT AVG(UserID) FROM hits;")
        self.assertIn("AVG(toFloat64(UserID))", ch)

    def test_normalization_and_numeric_validation(self) -> None:
        self.assertEqual(normalized([[date(2013, 7, 14), datetime(2013, 7, 14, tzinfo=timezone.utc)]]),
                         [["2013-07-14", "2013-07-14 00:00:00"]])
        self.assertTrue(equal_results(normalized([[Decimal("1.25")]]), [[1.25]], 1e-9, 1e-6))
        self.assertFalse(equal_results([[2**63]], [[2**63 + 1]], 1e-9, 1e-6))
        self.assertFalse(equal_results([["Google"]], [["google"]], 1e-9, 1e-6))
        self.assertFalse(equal_results([[None]], [[""]], 1e-9, 1e-6))
        with self.assertRaises(ValueError):
            normalized([[float("nan")]])

    def test_fingerprint_preserves_every_value_and_duplicates(self) -> None:
        rows = [[1, "雪\t\n\"\\N", date(2013, 7, 14)], [2, "", date(2013, 7, 14)]]
        first, reversed_rows, changed = Fingerprint(), Fingerprint(), Fingerprint()
        for row in rows:
            first.add(row)
        for row in reversed(rows):
            reversed_rows.add(row)
        for row in rows + [rows[0]]:
            changed.add(row)
        self.assertEqual(first.result(), reversed_rows.result())
        self.assertNotEqual(first.result(), changed.result())
        output = io.StringIO()
        writer = csv.writer(output, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerows(rows)
        loaded = list(csv.reader(io.StringIO(output.getvalue()), delimiter="\t"))
        self.assertEqual(loaded[0][1], rows[0][1])

    def test_clickhouse_insert_utc_not_host_local(self) -> None:
        naive = datetime(2013, 7, 14, 20, 38, 47)
        converted = clickhouse_insert_rows([[naive, date(2013, 7, 14), -7, ""]])[0]
        self.assertEqual(converted[0].tzinfo, timezone.utc)
        self.assertEqual(converted[0].timestamp(), 1373834327)
        self.assertEqual(normalized([converted]), normalized([[naive, date(2013, 7, 14), -7, ""]]))

    def test_old_clickhouse_schema(self) -> None:
        sql, reasons = adapt_schema("clickhouse", "CREATE OR REPLACE TABLE hits (x Int64) "
                                    "ENGINE=MergeTree SETTINGS fsync_after_insert = 1, "
                                    "auto_statistics_types=''; -- newer setting")
        self.assertNotIn("auto_statistics_types", sql)
        self.assertNotIn("OR REPLACE", sql)
        self.assertTrue(reasons)

    def test_license_preservation_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream = root / "upstream"
            upstream.mkdir()
            (upstream / "LICENSE").write_text("test license", encoding="ascii")
            notice = root / "notice.txt"
            notice.write_text("test attribution", encoding="ascii")
            target = root / "sql"
            preserve_license(upstream, target, notice)
            preserve_license(upstream, target, notice)
            self.assertEqual((target / "ClickBench-LICENSE.txt").read_text(), "test license")
            (target / "ClickBench-LICENSE.txt").write_text("unexpected change")
            with self.assertRaises(ValueError):
                preserve_license(upstream, target, notice)


if __name__ == "__main__":
    unittest.main()
