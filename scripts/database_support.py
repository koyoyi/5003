"""Frozen data and SQL preparation for E2/E3; no network operations."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from run_mechanism import sha256, write_json

DBMS = ("clickhouse", "postgresql", "mysql")


def hashlib_sql(sql: str) -> str:
    """Fingerprint SQL without loading optional database drivers."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


TIE_KEYS = {
    8: "AdvEngineID", 9: "RegionID", 10: "RegionID",
    11: "MobilePhoneModel", 12: "MobilePhone, MobilePhoneModel",
    13: "SearchPhrase", 14: "SearchPhrase", 15: "SearchEngineID, SearchPhrase",
    16: "UserID", 17: "UserID, SearchPhrase", 19: "UserID, m, SearchPhrase",
    22: "SearchPhrase", 23: "SearchPhrase",
    24: "CounterID, EventDate, UserID, WatchID",
    25: "SearchPhrase", 28: "CounterID", 29: "k",
    31: "SearchEngineID, ClientIP", 32: "WatchID, ClientIP", 33: "WatchID, ClientIP",
    34: "URL", 35: "URL", 36: "ClientIP", 37: "URL", 38: "Title", 39: "URL",
    40: "TraficSourceID, SearchEngineID, AdvEngineID, Src, Dst",
    41: "URLHash, EventDate", 42: "WindowClientWidth, WindowClientHeight",
}


def adapt_query(dbms: str, query_id: int, original: str) -> tuple[str, list[str]]:
    sql = original.strip().removesuffix(";")
    reasons = []
    if query_id in TIE_KEYS:
        if "ORDER BY" not in sql:
            raise ValueError(f"Expected ORDER BY in Q{query_id}")
        if " LIMIT " in sql:
            sql = sql.replace(" LIMIT ", f", {TIE_KEYS[query_id]} LIMIT ", 1)
        else:
            sql += ", " + TIE_KEYS[query_id]
        reasons.append("Deterministic secondary sort for tied rows/TOP-K, including offset boundaries.")
    if query_id == 18:
        sql = sql.replace(" LIMIT ", " ORDER BY UserID, SearchPhrase LIMIT ", 1)
        reasons.append("Freeze the otherwise unspecified grouped LIMIT output; changes sorting cost.")
    if query_id == 29:
        sql = sql.replace("'^https?", "'(?s)^https?")
        reasons.append("Explicit dot-all matching: otherwise PostgreSQL and RE2/ICU differ on embedded newlines.")
    if dbms == "clickhouse":
        if query_id == 4:
            sql = sql.replace("AVG(UserID)", "AVG(toFloat64(UserID))")
            reasons.append("Avoid observed Int64 accumulator overflow in ClickHouse 24.6 AVG(UserID).")
        if query_id in (28, 29):
            sql = re.sub(r"\blength\(", "lengthUTF8(", sql, flags=re.I)
            reasons.append("Use Unicode character length to match PostgreSQL and MySQL CHAR_LENGTH.")
        if query_id == 29:
            sql = sql.replace("REGEXP_REPLACE(", "replaceRegexpAll(")
            sql = sql.replace(r"www\.", r"www\\.").replace(r"'\1'", r"'\\1'")
            reasons.append("ClickHouse regexp function and SQL backslash escaping; anchored-match equivalence.")
        if query_id == 19:
            sql = re.sub(r"extract\(minute FROM EventTime\)", "toMinute(EventTime)", sql, flags=re.I)
            reasons.append("Explicit ClickHouse minute extraction.")
        if query_id == 43:
            sql = sql.replace("DATE_TRUNC(", "dateTrunc(")
            reasons.append("ClickHouse 24.6 minute truncation function spelling.")
    if dbms == "mysql":
        if query_id in (28, 29):
            sql = re.sub(r"\blength\(", "CHAR_LENGTH(", sql, flags=re.I)
            reasons.append("Unicode character length, not UTF-8 byte length.")
        if query_id == 29:
            sql = sql.replace(r"'\1'", "'$1'")
            reasons.append("ICU replacement backreference syntax; NO_BACKSLASH_ESCAPES preserves pattern.")
        if query_id == 43:
            sql = sql.replace("%Y-%m-%d %H:00:00", "%Y-%m-%d %H:%i:00")
            reasons.append("Official MySQL query truncates to hour; align to minute in the other DBMS.")
    return sql, reasons


def preserve_license(upstream: Path, sql_root: Path, notice: Path) -> None:
    sql_root.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for source, name in ((upstream / "LICENSE", "ClickBench-LICENSE.txt"),
                         (notice, "CLICKBENCH-NOTICE.txt")):
        destination = sql_root / name
        if destination.exists():
            if sha256(destination) != sha256(source):
                raise ValueError(f"Existing license/notice differs: {destination}")
        else:
            shutil.copy2(source, destination)
        hashes[name] = sha256(destination)
    write_json(sql_root / "license-annex.json", {
        "source": "https://github.com/ClickHouse/ClickBench",
        "license": "CC BY-NC-SA 4.0", "files": hashes,
    })


def schema_columns(sql: str) -> list[tuple[str, str]]:
    columns = re.findall(
        r"^\s+(\w+)\s+(BIGINT|SMALLINT|INTEGER|TEXT|TIMESTAMP|Date|CHAR|VARCHAR\(255\))\s+NOT NULL",
        sql, flags=re.M,
    )
    if len(columns) != 105:
        raise ValueError(f"Expected 105 full-schema columns, found {len(columns)}")
    return columns


def adapt_schema(dbms: str, original: str, table: str = "hits") -> tuple[str, list[str]]:
    if table not in ("hits", "hits_probe"):
        raise ValueError("Unexpected table name")
    sql = re.sub(r"\bhits\b", table, original, count=1)
    reasons = []
    if dbms == "clickhouse":
        sql = sql.replace("CREATE OR REPLACE TABLE", "CREATE TABLE")
        sql = re.sub(r"fsync_after_insert = 1,.*", "fsync_after_insert = 1;", sql, flags=re.S)
        reasons.append("Remove auto_statistics_types unavailable on 24.6; preserve fsync_after_insert=1.")
        reasons.append("CREATE TABLE refuses overwrite in this isolated experiment.")
    return sql, reasons


def scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite SQL result")
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise TypeError(f"Unsupported SQL value: {type(value)}")


def normalized(rows: Iterable[Sequence[Any]]) -> list[list[Any]]:
    return [[scalar(value) for value in row] for row in rows]


def clickhouse_insert_rows(rows: Iterable[Sequence[Any]]) -> list[list[Any]]:
    # ClickHouse's driver calls datetime.timestamp(); naive UTC would use host local time.
    return [[value.replace(tzinfo=timezone.utc)
             if isinstance(value, datetime) and value.tzinfo is None else value
             for value in row] for row in rows]


def numeric(value: Any) -> Decimal | None:
    if isinstance(value, dict) and set(value) == {"decimal"}:
        return Decimal(value["decimal"])
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    return None


def equal_results(a: list[list[Any]], b: list[list[Any]], relative: float, absolute: float) -> bool:
    if len(a) != len(b) or any(len(x) != len(y) for x, y in zip(a, b)):
        return False
    for row_a, row_b in zip(a, b):
        for x, y in zip(row_a, row_b):
            nx, ny = numeric(x), numeric(y)
            if nx is not None and ny is not None:
                # Integral values are exact unless a DBMS actually returned floating-point.
                if not isinstance(x, float) and not isinstance(y, float) and (
                        nx == nx.to_integral_value() and ny == ny.to_integral_value()):
                    if nx != ny:
                        return False
                elif abs(nx - ny) > max(Decimal(str(absolute)),
                                       Decimal(str(relative)) * max(abs(nx), abs(ny))):
                    return False
            elif x != y:
                return False
    return True


class Fingerprint:
    """Order-independent full-row fingerprint; counts duplicates as well."""

    def __init__(self) -> None:
        self.count = 0
        self.total = 0
        self.xor = 0

    def add(self, row: Sequence[Any]) -> None:
        encoded = json.dumps([scalar(v) for v in row], ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        word = int.from_bytes(hashlib.sha256(encoded).digest(), "big")
        self.count += 1
        self.total = (self.total + word) % (1 << 256)
        self.xor ^= word

    def result(self) -> dict[str, Any]:
        return {"rows": self.count, "sha256_sum_mod_256": f"{self.total:064x}",
                "sha256_xor": f"{self.xor:064x}"}


def prepare_clickbench(source: Path, output: Path, columns: list[tuple[str, str]]) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    output.mkdir(exist_ok=False)
    parquet = pq.ParquetFile(source)
    names = [name for name, _ in columns]
    if parquet.schema_arrow.names != names or parquet.metadata.num_rows != 1000000:
        raise ValueError("Frozen shard must contain 1,000,000 rows and the complete official schema")
    complete, probe = Fingerprint(), Fingerprint()
    integer_ranges = {"SMALLINT": (-2**15, 2**15 - 1), "INTEGER": (-2**31, 2**31 - 1),
                      "BIGINT": (-2**63, 2**63 - 1)}
    with (output / "hits.csv").open("x", encoding="utf-8", newline="") as full_file, \
            (output / "probe.csv").open("x", encoding="utf-8", newline="") as probe_file:
        full_writer = csv.writer(full_file, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\n")
        probe_writer = csv.writer(probe_file, delimiter="\t", quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer = None
        try:
            for raw in parquet.iter_batches(batch_size=16384):
                arrays = []
                for i, (_, kind) in enumerate(columns):
                    array = raw.column(i)
                    if array.null_count:
                        raise ValueError(f"NULL in NOT NULL column {names[i]}")
                    if kind in ("TEXT", "CHAR", "VARCHAR(255)"):
                        array = pc.cast(array, pa.string(), safe=True)
                    elif kind == "TIMESTAMP":
                        array = pc.cast(array, pa.timestamp("s"), safe=True)
                    elif kind == "Date":
                        array = pc.cast(pc.cast(array, pa.int32()), pa.date32())
                    arrays.append(array)
                batch = pa.RecordBatch.from_arrays(arrays, names=names)
                batch.validate(full=True)
                if writer is None:
                    writer = pq.ParquetWriter(output / "logical.parquet", batch.schema)
                rows = list(zip(*(column.to_pylist() for column in batch.columns)))
                for row in rows:
                    for (name, kind), value in zip(columns, row):
                        if isinstance(value, str):
                            if "\0" in value:
                                raise ValueError(f"Embedded NUL unsupported by PostgreSQL TEXT: {name}")
                            limit = 255 if kind == "VARCHAR(255)" else 1 if kind == "CHAR" else None
                            if limit is not None and len(value) > limit:
                                raise ValueError(f"String exceeds declared limit: {name}")
                            if kind == "TEXT" and len(value.encode("utf-8")) > 65535:
                                raise ValueError(f"MySQL TEXT overflow: {name}")
                        if kind in integer_ranges and not integer_ranges[kind][0] <= value <= integer_ranges[kind][1]:
                            raise ValueError(f"Integer overflow: {name}")
                        if kind == "TIMESTAMP" and not datetime(1970, 1, 1, 0, 0, 1) <= value <= datetime(2038, 1, 19, 3, 14, 7):
                            raise ValueError(f"Outside MySQL TIMESTAMP range: {name}")
                    complete.add(row)
                    converted = [scalar(value) for value in row]
                    full_writer.writerow(converted)
                    if complete.count <= 100000:
                        probe.add(row)
                        probe_writer.writerow(converted)
                writer.write_batch(batch)
                if complete.count % 262144 == 0:
                    print(f"Converted {complete.count} rows with full-row fingerprints", flush=True)
        finally:
            if writer is not None:
                writer.close()
    if complete.count != 1000000:
        raise ValueError("Wrong prepared row count")
    manifest = {
        "source_file": str(source), "source_bytes": source.stat().st_size,
        "source_sha256": sha256(source), "source_rows": parquet.metadata.num_rows,
        "source_schema": str(parquet.schema_arrow),
        "complete_fingerprint": complete.result(), "probe_fingerprint": probe.result(),
        "logical_parquet_sha256": sha256(output / "logical.parquet"),
        "csv_sha256": sha256(output / "hits.csv"), "probe_csv_sha256": sha256(output / "probe.csv"),
        "conversion": "strict UTF-8; epoch-day Date; epoch-second UTC TIMESTAMP; "
                      "quoted tab-separated CSV preserving empty strings, quotes, tabs, newlines and backslashes",
        "null_policy": "reject: official frozen schema is NOT NULL; no silent defaults",
        "columns": columns,
    }
    write_json(output / "manifest.json", manifest)
    return manifest
