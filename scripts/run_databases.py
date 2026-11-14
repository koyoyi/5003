"""Run isolated E2 native validation and E3 deterministic ClickBench."""

from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import clickhouse_connect
import psycopg
import pymysql
import pyarrow.parquet as pq
from clickhouse_connect.driver.exceptions import ClickHouseError

from database_support import (
    DBMS, Fingerprint, adapt_query, adapt_schema, clickhouse_insert_rows, equal_results, normalized,
    prepare_clickbench, preserve_license, schema_columns, hashlib_sql,
)
from run_mechanism import execute, sha256, write_csv, write_json

QUERY_ERRORS = (ClickHouseError, psycopg.Error, pymysql.MySQLError)


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], text=True, capture_output=True,
                            timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f"Docker command failed: {args[0]}\n{result.stderr}")
    return result.stdout.strip()


def container_evidence(name: str) -> dict[str, Any]:
    details = json.loads(docker("inspect", name))[0]
    image = json.loads(docker("image", "inspect", details["Image"]))[0]
    host = details["HostConfig"]
    return {
        "name": name, "id": details["Id"], "image_id": details["Image"],
        "image_digest": image["RepoDigests"], "memory_bytes": host["Memory"],
        "nano_cpus": host["NanoCpus"], "port_bindings": host["PortBindings"],
        "state": details["State"], "mounts": details["Mounts"],
    }


class Database:
    def __init__(self, dbms: str, config: dict[str, Any], password: str) -> None:
        self.dbms = dbms
        self.config = config
        self.password = password
        port = config["ports"][dbms]
        if dbms == "clickhouse":
            self.connection = clickhouse_connect.get_client(
                host="127.0.0.1", port=port, username="repro", password=password,
                database="benchmark", query_retries=0, connect_timeout=5,
                send_receive_timeout=75, settings={
                    "max_threads": 2, "max_insert_threads": 2, "max_memory_usage": 3221225472,
                    "max_execution_time": config["timeout_s"], "use_query_cache": 0,
                    "count_distinct_implementation": "uniqExact", "log_queries": 1,
                },
            )
            self.identity = "repro"
        elif dbms == "postgresql":
            self.connection = psycopg.connect(
                host="127.0.0.1", port=port, user="repro", password=password,
                dbname="benchmark", autocommit=True, connect_timeout=5,
            )
            self.identity = self.connection.info.backend_pid
            self.command("SET statement_timeout = '60s'")
        elif dbms == "mysql":
            self.connection = pymysql.connect(
                host="127.0.0.1", port=port, user="repro", password=password,
                database="benchmark", charset="utf8mb4", autocommit=True,
                local_infile=True, connect_timeout=5, read_timeout=75, write_timeout=600,
            )
            self.command("SET SESSION sql_mode = 'STRICT_ALL_TABLES,NO_BACKSLASH_ESCAPES'")
            self.command("SET NAMES utf8mb4 COLLATE utf8mb4_0900_bin")
            self.command(f"SET SESSION div_precision_increment = {int(config['mysql_div_precision_increment'])}")
            self.command("SET SESSION time_zone = '+00:00'")
            self.command("SET SESSION max_execution_time = 60000")
            self.identity = self.query("SELECT CONNECTION_ID()")[0][0][0]
        else:
            raise ValueError(dbms)

    def close(self) -> None:
        self.connection.close()

    def command(self, sql: str) -> None:
        if self.dbms == "clickhouse":
            self.connection.command(sql)
        else:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)

    def query(self, sql: str) -> tuple[list[list[Any]], dict[str, Any]]:
        if self.dbms == "clickhouse":
            result = self.connection.query(sql)
            return normalized(result.result_rows), result.summary
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            return normalized(cursor.fetchall()), {}

    def version(self) -> str:
        sql = "SELECT version()" if self.dbms != "mysql" else "SELECT VERSION()"
        return str(self.query(sql)[0][0][0])

    def settings(self) -> list[list[Any]]:
        if self.dbms == "clickhouse":
            return self.query(
                "SELECT name,value FROM system.settings WHERE name IN "
                "('max_threads','max_insert_threads','max_memory_usage','max_execution_time',"
                "'use_query_cache','count_distinct_implementation','log_queries') ORDER BY name")[0]
        if self.dbms == "postgresql":
            return self.query(
                "SELECT name,setting FROM pg_settings WHERE name IN "
                "('max_parallel_workers_per_gather','shared_buffers','work_mem','jit',"
                "'statement_timeout','lc_collate','lc_ctype','TimeZone') ORDER BY name")[0]
        return self.query(
            "SELECT @@version,@@sql_mode,@@collation_connection,@@collation_server,"
            "@@time_zone,@@max_execution_time,@@innodb_buffer_pool_size,@@div_precision_increment")[0]

    def fingerprint(self, table: str) -> dict[str, Any]:
        if table not in ("hits", "hits_probe"):
            raise ValueError(table)
        fingerprint = Fingerprint()
        if self.dbms == "clickhouse":
            with self.connection.query_row_block_stream(
                    f"SELECT * FROM {table}", settings={"max_execution_time": 600}) as stream:
                for block in stream:
                    for row in block:
                        fingerprint.add(row)
        elif self.dbms == "postgresql":
            self.command("SET statement_timeout = '600s'")
            try:
                with self.connection.transaction():
                    with self.connection.cursor(name="integrity_check") as cursor:
                        cursor.execute(f"SELECT * FROM {table}")
                        while rows := cursor.fetchmany(4096):
                            for row in rows:
                                fingerprint.add(row)
            finally:
                self.command("SET statement_timeout = '60s'")
        else:
            self.command("SET SESSION max_execution_time = 600000")
            try:
                with self.connection.cursor(pymysql.cursors.SSCursor) as cursor:
                    cursor.execute(f"SELECT * FROM {table}")
                    while rows := cursor.fetchmany(4096):
                        for row in rows:
                            fingerprint.add(row)
            finally:
                self.command("SET SESSION max_execution_time = 60000")
        return fingerprint.result()

    def import_data(self, table: str, data: Path, columns: list[str], limit: int) -> None:
        if table not in ("hits", "hits_probe"):
            raise ValueError(table)
        if self.dbms == "clickhouse":
            remaining = limit
            for batch in pq.ParquetFile(data / "logical.parquet").iter_batches(batch_size=16384):
                batch = batch.slice(0, min(batch.num_rows, remaining))
                self.connection.insert(table, clickhouse_insert_rows(zip(*(c.to_pylist() for c in batch.columns))),
                                       column_names=columns)
                remaining -= batch.num_rows
                if remaining == 0:
                    break
            if remaining:
                raise ValueError("Insufficient rows for ClickHouse import")
            self.command(f"OPTIMIZE TABLE {table} FINAL")
        elif self.dbms == "postgresql":
            filename = data / ("probe.csv" if limit == 100000 else "hits.csv")
            self.command("SET statement_timeout = '600s'")
            try:
                with self.connection.cursor() as cursor:
                    with cursor.copy(f"COPY {table} FROM STDIN WITH "
                                     "(FORMAT CSV, DELIMITER E'\\t', QUOTE '\"', ESCAPE '\"')") as copy:
                        with filename.open("rb") as source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                copy.write(chunk)
                self.command(f"ANALYZE {table}")
            finally:
                self.command("SET statement_timeout = '60s'")
        else:
            filename = data / ("probe.csv" if limit == 100000 else "hits.csv")
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"LOAD DATA LOCAL INFILE %s INTO TABLE {table} CHARACTER SET utf8mb4 "
                    "FIELDS TERMINATED BY '\t' ENCLOSED BY '\"' ESCAPED BY '\"' "
                    "LINES TERMINATED BY '\n'", (str(filename),))
                cursor.execute("SHOW WARNINGS")
                warnings = cursor.fetchall()
                if warnings:
                    raise ValueError(f"MySQL import warnings (refusing lossy import): {warnings[:10]}")
            self.command(f"ANALYZE TABLE {table}")

    def cancel(self, marker: str) -> None:
        control = Database(self.dbms, self.config, self.password)
        try:
            if self.dbms == "clickhouse":
                control.command(f"KILL QUERY WHERE startsWith(query, '/*{marker}*/') SYNC")
            elif self.dbms == "postgresql":
                result = control.query(f"SELECT pg_cancel_backend({int(self.identity)})")[0]
                if not result:
                    raise RuntimeError("PostgreSQL cancellation returned no evidence")
            else:
                with control.connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM information_schema.processlist WHERE ID=%s",
                                   (int(self.identity),))
                    if cursor.fetchone()[0]:
                        cursor.execute(f"KILL QUERY {int(self.identity)}")
        finally:
            control.close()


def ready(dbms: str, config: dict[str, Any], password: str, log: Path) -> Database:
    deadline = time.monotonic() + 120
    with log.open("a", encoding="utf-8") as output:
        while True:
            try:
                db = Database(dbms, config, password)
                if db.query("SELECT 1")[0] != [[1]]:
                    raise RuntimeError("Readiness query returned incorrect result")
                return db
            except (*QUERY_ERRORS, OSError) as error:
                output.write(json.dumps({"dbms": dbms, "readiness_error": str(error)}) + "\n")
                output.flush()
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{dbms} not responsive after 120s; see {log}") from error
                time.sleep(2)


def create_containers(suite: Path, config: dict[str, Any], password: str) -> dict[str, str]:
    names = {dbms: f"chrepro-{suite.name}-{dbms}" for dbms in DBMS}
    for port in config["ports"].values():
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", port))
    for dbms in DBMS:
        filename = suite / f".{dbms}.env"
        fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            if dbms == "clickhouse":
                output.write(f"CLICKHOUSE_USER=repro\nCLICKHOUSE_PASSWORD={password}\n"
                             "CLICKHOUSE_DB=benchmark\nCLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1\nTZ=UTC\n")
            elif dbms == "postgresql":
                output.write(f"POSTGRES_USER=repro\nPOSTGRES_PASSWORD={password}\n"
                             "POSTGRES_DB=benchmark\nPOSTGRES_INITDB_ARGS=--locale=C --encoding=UTF8\nTZ=UTC\n")
            else:
                output.write(f"MYSQL_ROOT_PASSWORD={secrets.token_hex(24)}\nMYSQL_USER=repro\n"
                             f"MYSQL_PASSWORD={password}\nMYSQL_DATABASE=benchmark\nTZ=UTC\n")
        port = str(config["ports"][dbms])
        target = {"clickhouse": "8123", "postgresql": "5432", "mysql": "3306"}[dbms]
        args = [
            "create", "--pull=never", "--name", names[dbms], "--cpus", "2",
            "--memory", str(config["memory_bytes"]), "--pids-limit", "512",
            "--shm-size", "512m", "--env-file", str(filename),
            "--publish", f"127.0.0.1:{port}:{target}",
        ]
        if dbms == "clickhouse":
            args += [
                "--ulimit", "nofile=262144:262144",
                "--mount", f"type=bind,src={suite / 'artifacts' / 'clickhouse-limited.xml'},"
                "dst=/etc/clickhouse-server/config.d/repro-limited.xml,readonly",
                config["images"][dbms],
            ]
        elif dbms == "postgresql":
            args += [
                config["images"][dbms], "-c", "timezone=UTC",
                "-c", "max_parallel_workers_per_gather=2", "-c", "shared_buffers=512MB",
                "-c", "work_mem=64MB", "-c", "jit=off",
            ]
        else:
            args += [
                config["images"][dbms], "--character-set-server=utf8mb4",
                "--collation-server=utf8mb4_0900_bin", "--local-infile=1",
                "--innodb-buffer-pool-size=1073741824", "--max-allowed-packet=134217728",
                "--default-time-zone=+00:00",
            ]
        docker(*args)
    return names


def native_validation(db: Database, suite: Path, e1: Path, project: Path,
                      existing_table: bool = False) -> dict[str, Any]:
    artifacts = suite / "artifacts" / "native-validation"
    artifacts.mkdir()
    data = e1 / "A-v1-seed20260930-N1000000.tsv"
    executable = e1 / "build" / "mini_index"
    source_meta = json.loads((e1 / "artifacts" / "suite.json").read_text())
    manifest = next(d for d in source_meta["datasets"] if d["rows"] == 1000000)
    if sha256(data) != manifest["input_sha256"]:
        raise ValueError("E1 data checksum changed")
    ddl = (project / "sql" / "mechanism" / "create.sql").read_text()
    if not existing_table:
        for statement in ddl.split(";"):
            if statement.strip():
                db.command(statement)
    (artifacts / "create.sql").write_text(ddl)
    if not existing_table:
        with data.open(newline="") as source:
            reader = csv.DictReader(source, delimiter="\t")
            block = []
            for row in reader:
                block.append([int(row["row_id"]), int(row["key"]), int(row["value"])])
                if len(block) == 16384:
                    db.connection.insert("mechanism.rows", block, column_names=["row_id", "key", "value"])
                    block.clear()
            if block:
                db.connection.insert("mechanism.rows", block, column_names=["row_id", "key", "value"])
        db.command("OPTIMIZE TABLE mechanism.rows FINAL")
    descriptions = [
        ("narrow", "key >= 124875 AND key < 125125", 124875, 125125),
        ("medium", "key >= 112500 AND key < 137500", 112500, 137500),
        ("near_full", "key >= 0 AND key < 247500", 0, 247500),
        ("non_key", "value >= 90", None, None),
    ]
    expected = {name: {"count": 0, "sum": 0, "ids": []} for name, *_ in descriptions}
    with data.open(newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            row_id, key, value = int(row["row_id"]), int(row["key"]), int(row["value"])
            for name, _, lower, upper in descriptions:
                if (value >= 90) if lower is None else (lower <= key < upper):
                    expected[name]["count"] += 1
                    expected[name]["sum"] += value
                    expected[name]["ids"].append(row_id)
    records = []
    for name, predicate, lower, upper in descriptions:
        oracle = expected[name]
        oracle["ids"].sort()
        if lower is not None:
            for method in ("full", "index"):
                result = json.loads(execute(
                    [str(executable), "query", str(e1 / "N1000000-G8192"), method,
                     "range", str(lower), str(upper), "ids"],
                    suite / f"native-{name}-{method}.log"))
                if (result["count"] != oracle["count"] or result["sum"] != oracle["sum"] or
                        sorted(result["row_ids"]) != oracle["ids"]):
                    raise ValueError("E1/native independent oracle mismatch")
        ids = db.query(f"SELECT row_id FROM mechanism.rows WHERE {predicate} ORDER BY row_id")[0]
        if [row[0] for row in ids] != oracle["ids"]:
            raise ValueError(f"Native full row_id mismatch: {name}")
        sql = f"SELECT count(), sum(value) FROM mechanism.rows WHERE {predicate}"
        (artifacts / f"{name}.sql").write_text(sql + ";\n")
        result, summary = db.query(sql)
        if result != [[oracle["count"], oracle["sum"]]]:
            raise ValueError(f"Native count/sum mismatch: {name}")
        query_id = summary["query_id"]
        plan = db.query("EXPLAIN indexes = 1 " + sql)[0]
        (artifacts / f"{name}-explain.txt").write_text("\n".join(str(row[0]) for row in plan) + "\n")
        db.command("SYSTEM FLUSH LOGS")
        stats = db.query(
            "SELECT read_rows,read_bytes,query_duration_ms FROM system.query_log "
            f"WHERE query_id='{query_id}' AND type='QueryFinish' "
            "ORDER BY event_time_microseconds DESC LIMIT 1")[0]
        if len(stats) != 1:
            raise ValueError("Missing native QueryFinish statistics")
        records.append({
            "query_name": name, "query_id": query_id, "dataset_sha256": manifest["input_sha256"],
            "dataset_rows": 1000000, "result_count": oracle["count"], "result_sum": oracle["sum"],
            "read_rows": stats[0][0], "read_bytes": stats[0][1], "query_duration_ms": stats[0][2],
            "validation_status": "passed", "row_ids_validated": True,
        })
        print(f"E2 {name}: validated, read_rows={stats[0][0]}", flush=True)
    info = {
        "e1_suite": e1.name, "version": db.version(), "settings": db.settings(),
        "parts": db.query("SELECT name,rows,marks FROM system.parts "
                          "WHERE database='mechanism' AND table='rows' AND active")[0],
        "schema": db.query("SHOW CREATE TABLE mechanism.rows")[0],
        "records": records,
        "interpretation": "Native server statistics only; no C++ versus ClickHouse absolute speed claim.",
    }
    write_csv(artifacts / "queries.csv", records)
    write_json(artifacts / "metadata.json", info)
    return info


def query_record(
    db: Database, sql: str, reference: list[list[Any]] | None, config: dict[str, Any],
) -> tuple[dict[str, Any], list[list[Any]] | None]:
    marker = "repro_" + uuid.uuid4().hex
    started = time.perf_counter()
    try:
        rows, summary = db.query(f"/*{marker}*/ {sql}")
    except QUERY_ERRORS as error:
        observed = time.perf_counter() - started
        cancellation = "targeted-cancel-issued"
        try:
            db.cancel(marker)
        except QUERY_ERRORS as cancellation_error:
            cancellation = f"targeted-cancel-failed: {cancellation_error}"
        message = str(error)
        is_timeout = any(token in message.lower() for token in (
            "timeout", "time limit", "statement timeout", "maximum statement execution",
            "3024", "code: 159", "canceling statement",
        ))
        return {
            "status": "timeout" if is_timeout else "error", "elapsed_s": None,
            "observed_elapsed_s": observed, "server_elapsed_s": None, "exit_code": 1,
            "result_rows": None, "validation_status": "unavailable", "error": message,
            "server_query_id": None, "cancellation": cancellation,
        }, None
    observed = time.perf_counter() - started
    matched = reference is not None and equal_results(
        rows, reference, config["float_relative_tolerance"], config["float_absolute_tolerance"])
    return {
        "status": "success" if matched else "validation_failed", "elapsed_s": observed if matched else None,
        "observed_elapsed_s": observed,
        "server_elapsed_s": int(summary["elapsed_ns"]) / 1e9 if summary.get("elapsed_ns") else None,
        "exit_code": 0 if matched else 1, "result_rows": len(rows),
        "validation_status": "passed" if matched else "failed",
        "error": "" if matched else "No validated three-system reference or result mismatch",
        "server_query_id": summary.get("query_id"), "cancellation": None,
    }, rows


def timeout_probe(db: Database) -> dict[str, Any]:
    marker = "timeout_probe_" + uuid.uuid4().hex
    if db.dbms == "postgresql":
        db.command("SET statement_timeout = '1s'")
        sql = "SELECT pg_sleep(5)"
    elif db.dbms == "mysql":
        db.command("SET SESSION max_execution_time = 1000")
        sql = "SELECT SUM(a.ResolutionWidth + b.ResolutionWidth) FROM hits a CROSS JOIN hits b"
    else:
        sql = "SELECT sum(number) FROM numbers(100000000000)"
    started = time.perf_counter()
    evidence: dict[str, Any]
    try:
        try:
            if db.dbms == "clickhouse":
                db.connection.query(f"/*{marker}*/ {sql}", settings={
                    "max_execution_time": 1, "timeout_before_checking_execution_speed": 0,
                })
            else:
                db.query(f"/*{marker}*/ {sql}")
        except QUERY_ERRORS as error:
            elapsed = time.perf_counter() - started
            message = str(error)
            if not any(token in message.lower() for token in (
                    "timeout", "statement timeout", "maximum statement execution", "code: 159")):
                raise RuntimeError(f"Timeout probe failed for an unrelated reason: {message}") from error
            if not 0.5 <= elapsed <= 5:
                raise RuntimeError(f"One-second native timeout outside allowed observed window: {elapsed}")
            db.cancel(marker)
            evidence = {"configured_timeout_s": 1, "observed_elapsed_s": elapsed,
                        "native_error": message, "targeted_cancellation_issued": True}
        else:
            raise RuntimeError("Native timeout probe completed unexpectedly without cancellation")
    finally:
        if db.dbms == "postgresql":
            db.command("SET statement_timeout = '60s'")
        elif db.dbms == "mysql":
            db.command("SET SESSION max_execution_time = 60000")
    if db.dbms == "clickhouse":
        active = db.query(
            f"SELECT count() FROM system.processes WHERE startsWith(query, '/*{marker}*/')")[0][0][0]
    elif db.dbms == "postgresql":
        active = db.query(
            f"SELECT count(*) FROM pg_stat_activity WHERE pid={int(db.identity)} "
            f"AND state='active' AND left(query,{len(marker) + 4})='/*{marker}*/'")[0][0][0]
    else:
        active = db.query(
            "SELECT COUNT(*) FROM information_schema.processlist "
            f"WHERE ID={int(db.identity)} AND LEFT(INFO,{len(marker) + 4})='/*{marker}*/'")[0][0][0]
    if active != 0:
        raise RuntimeError("Timed-out server query is still active")
    evidence["active_queries_after"] = active
    return evidence


def run(project: Path, work_root: Path, e1: Path, prepared_data: Path | None = None,
        reuse_databases: Path | None = None) -> Path:
    if sys.platform != "linux" or work_root.resolve().is_relative_to(Path("/mnt")):
        raise ValueError("Database experiments require the WSL Linux filesystem")
    config = json.loads((project / "configs" / "clickbench.json").read_text())
    if (config["cpu_limit"] != 2 or config["memory_bytes"] != 4294967296 or
            config["timeout_s"] != 60 or config["runs"] != 3 or
            config["query_variant"] != "deterministic-ties-and-dialect-normalization-v2" or
            config["mysql_div_precision_increment"] != 12):
        raise ValueError("Unapproved database protocol")
    upstream = Path.home() / "clickhouse-repro" / "clickbench"
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(upstream), "status", "--porcelain"], text=True)
    if commit != config["clickbench_commit"] or dirty:
        raise ValueError("ClickBench commit/worktree differs from the frozen workload")
    original_container = container_evidence("clickhouse-repro")
    suite_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    suite = work_root / suite_id
    suite.mkdir(parents=True, exist_ok=False)
    artifacts = suite / "artifacts"
    artifacts.mkdir()
    write_json(artifacts / "config.json", config)
    shutil.copy2(project / "configs" / "clickhouse-limited.xml", artifacts / "clickhouse-limited.xml")
    sql_root = artifacts / "sql"
    sql_root.mkdir()
    preserve_license(upstream, sql_root, project / "sql" / "CLICKBENCH-NOTICE.txt")
    source_hashes = {str(path.relative_to(project)): sha256(path)
                     for directory in ("scripts", "configs", "sql")
                     for path in sorted((project / directory).rglob("*")) if path.is_file()
                     and "__pycache__" not in path.parts}
    write_json(artifacts / "source-sha256.json", source_hashes)
    for relative, digest in source_hashes.items():
        source_file = project / relative
        snapshot_file = suite / "source" / relative
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, snapshot_file)
        if sha256(snapshot_file) != digest:
            raise ValueError("Source changed during snapshot")
    shutil.copy2(project / "requirements-databases.txt", suite / "source" / "requirements-databases.txt")
    plans = {}
    schemas = {}
    for dbms in DBMS:
        directory = sql_root / dbms
        directory.mkdir()
        original_schema = (upstream / dbms / "create.sql").read_text()
        schemas[dbms], schema_reasons = adapt_schema(dbms, original_schema)
        (directory / "create-original.sql").write_text(original_schema)
        (directory / "create-adapted.sql").write_text(schemas[dbms])
        originals = [line.strip() for line in (upstream / dbms / "queries.sql").read_text().splitlines()
                     if line.strip()]
        if len(originals) != 43:
            raise ValueError(f"{dbms}: expected 43 official queries")
        plans[dbms] = []
        for query_id, original in enumerate(originals, 1):
            adapted, reasons = adapt_query(dbms, query_id, original)
            (directory / f"Q{query_id:02d}-original.sql").write_text(original + "\n")
            (directory / f"Q{query_id:02d}-adapted.sql").write_text(adapted + ";\n")
            plans[dbms].append({"query_id": query_id, "sql": adapted, "reasons": reasons,
                                "original_sha256": hashlib_sql(original),
                                "query_sha256": hashlib_sql(adapted)})
        write_json(directory / "adaptations.json", {"schema": schema_reasons, "queries": plans[dbms]})
    previous = None
    if reuse_databases is None:
        password = secrets.token_hex(24)
        names = create_containers(suite, config, password)
    else:
        reuse_databases = reuse_databases.resolve()
        previous = json.loads((reuse_databases / "artifacts" / "suite.json").read_text())
        if previous["status"] not in ("complete", "complete-with-recorded-failures"):
            raise ValueError("Cannot reuse an incomplete database suite")
        for field in ("images", "ports", "cpu_limit", "memory_bytes", "timeout_s"):
            if previous["config"][field] != config[field]:
                raise ValueError(f"Reused database protocol differs: {field}")
        names = json.loads((reuse_databases / "containers.json").read_text())
        for dbms, name in names.items():
            if name != f"chrepro-{reuse_databases.name}-{dbms}" or container_evidence(name)["state"]["Running"]:
                raise ValueError("Reuse requires owned, stopped experiment containers")
        password = next(line.split("=", 1)[1]
                        for line in (reuse_databases / ".clickhouse.env").read_text().splitlines()
                        if line.startswith("CLICKHOUSE_PASSWORD="))
    write_json(suite / "containers.json", names)
    metadata = {
        "suite_id": suite_id, "status": "running", "clickbench_commit": commit,
        "clickbench_commit_info": subprocess.check_output(
            ["git", "-C", str(upstream), "log", "-1", "--format=%H %ci %s"], text=True).strip(),
        "config": config, "versions": {}, "containers": {},
        "driver_versions": {name: importlib.metadata.version(name) for name in
                            ("pyarrow", "clickhouse-connect", "psycopg", "PyMySQL")},
        "work_directory": str(suite), "e1_work_directory": str(e1),
        "validation": {}, "preflight": {}, "imports": {}, "timeout_probes": {},
        "original_container_before": original_container,
        "cpu_info": Path("/proc/cpuinfo").read_text(), "memory_info": Path("/proc/meminfo").read_text(),
    }
    if previous is not None:
        metadata["reused_database_suite"] = str(reuse_databases)
    write_json(artifacts / "suite.json", metadata)
    execute([sys.executable, "-m", "pip", "freeze"], artifacts / "pip-freeze.txt")
    source = Path.home() / "clickhouse-repro" / "data" / "clickbench" / config["data_file"]
    if prepared_data is None:
        data = suite / "data"
        manifest = prepare_clickbench(source, data, schema_columns(schemas["postgresql"]))
    else:
        data = prepared_data.resolve()
        manifest = json.loads((data / "manifest.json").read_text())
        if sha256(source) != manifest["source_sha256"]:
            raise ValueError("Prepared cache belongs to a different raw dataset")
        for name, field in (("logical.parquet", "logical_parquet_sha256"),
                            ("hits.csv", "csv_sha256"), ("probe.csv", "probe_csv_sha256")):
            if sha256(data / name) != manifest[field]:
                raise ValueError(f"Prepared cache checksum mismatch: {name}")
        if manifest["source_rows"] != 1000000 or manifest["columns"] != [
                list(column) for column in schema_columns(schemas["postgresql"])]:
            raise ValueError("Prepared cache schema/row count mismatch")
        metadata["prepared_data_reused_from"] = str(data)
    manifest.update(data_url=config["data_url"], acquisition_date="unknown-existing-local-file",
                    source_mtime_utc=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat())
    write_json(artifacts / "data-manifest.json", manifest)
    validation_results: dict[str, dict[int, list[list[Any]] | None]] = {}
    for dbms in DBMS:
        name = names[dbms]
        docker("start", name)
        db = None
        try:
            db = ready(dbms, config, password, artifacts / "readiness.jsonl")
            metadata["versions"][dbms] = db.version()
            metadata["containers"][dbms] = container_evidence(name)
            write_json(artifacts / f"{dbms}-settings.json", db.settings())
            if dbms == "clickhouse":
                metadata["native_validation"] = native_validation(
                    db, suite, e1, project, existing_table=previous is not None)
            for table, limit, expected in (
                    ("hits_probe", 100000, manifest["probe_fingerprint"]),
                    ("hits", 1000000, manifest["complete_fingerprint"])):
                started = time.perf_counter()
                if previous is None:
                    schema, _ = adapt_schema(dbms, (upstream / dbms / "create.sql").read_text(), table)
                    db.command(schema)
                    db.import_data(table, data, [name for name, _ in manifest["columns"]], limit)
                import_s = time.perf_counter() - started
                actual = db.fingerprint(table)
                if actual != expected:
                    write_json(artifacts / f"{dbms}-{table}-integrity-failure.json",
                               {"expected": expected, "actual": actual})
                    raise ValueError(f"{dbms} {table}: full-row data fingerprint mismatch")
                if previous is None:
                    import_record = {"elapsed_s": import_s, "fingerprint": actual, "import_reused": False}
                else:
                    old_record = previous["imports"][f"{dbms}-{table}"]
                    if old_record["fingerprint"] != actual:
                        raise ValueError("Reused database changed since its verified import")
                    import_record = {**old_record, "fingerprint": actual, "import_reused": True,
                                     "original_import_suite": previous["suite_id"]}
                metadata["imports"][f"{dbms}-{table}"] = import_record
                print(f"{dbms} {table}: {limit} rows "
                      f"{'imported' if previous is None else 'reused'} and every-column fingerprint verified",
                      flush=True)
            metadata["timeout_probes"][dbms] = timeout_probe(db)
            print(f"{dbms}: native one-second timeout and server termination verified", flush=True)
            validation_results[dbms] = {}
            checks = []
            for plan in plans[dbms]:
                record, rows = query_record(db, plan["sql"], [], config)
                validation_results[dbms][plan["query_id"]] = rows
                # Cross-system validation is performed only after all three complete.
                checks.append({"query_id": plan["query_id"], "execution_status":
                               "success" if rows is not None else record["status"],
                               "error": record["error"] if rows is None else "", "result": rows})
            write_json(artifacts / f"{dbms}-validation-results.json", checks)
            write_json(artifacts / "suite.json", metadata)
        finally:
            if db is not None:
                db.close()
            docker("stop", "--time", "30", name)
    references = {}
    coverage = []
    for query_id in range(1, 44):
        results = [validation_results[dbms][query_id] for dbms in DBMS]
        passed = all(result is not None for result in results)
        if passed:
            passed = all(equal_results(results[0], result, config["float_relative_tolerance"],
                                       config["float_absolute_tolerance"]) for result in results[1:])
        references[query_id] = results[0] if passed else None
        coverage.append({"query_id": query_id, "validation_status": "passed" if passed else "failed"})
    write_csv(artifacts / "query-validation.csv", coverage)
    records = []
    with (artifacts / "status-records.jsonl").open("x", encoding="utf-8") as journal:
        for dbms in DBMS:
            name = names[dbms]
            docker("start", name)
            db = None
            try:
                db = ready(dbms, config, password, artifacts / "readiness.jsonl")
                for plan in plans[dbms]:
                    for run_no in range(1, 4):
                        record, rows = query_record(db, plan["sql"], references[plan["query_id"]], config)
                        result_path = f"{dbms}-Q{plan['query_id']:02d}-R{run_no}.json"
                        write_json(artifacts / result_path, {"record": record, "result": rows})
                        combined = {
                            "suite_id": suite_id, "dbms": dbms, "db_version": metadata["versions"][dbms],
                            "image_digest": ";".join(metadata["containers"][dbms]["image_digest"]),
                            "query_id": plan["query_id"], "query_sha256": plan["query_sha256"],
                            "run_no": run_no, "cache_protocol": "first_run" if run_no == 1 else "repeat_run",
                            "cache_details": config["cache_protocol"], "timing_scope": config["timing_scope"],
                            "dataset_rows": manifest["source_rows"], "dataset_sha256": manifest["source_sha256"],
                            "clickbench_commit": commit, **record,
                            "timestamp": datetime.now(timezone.utc).isoformat(), "log_path": result_path,
                            "connection_reused": True, "timeout_s": 60,
                        }
                        records.append(combined)
                        journal.write(json.dumps(combined, allow_nan=False) + "\n")
                        journal.flush()
                    print(f"E3 {dbms} Q{plan['query_id']:02d}: "
                          f"{[r['status'] for r in records[-3:]]}", flush=True)
            finally:
                if db is not None:
                    db.close()
                docker("stop", "--time", "30", name)
                metadata["containers"][dbms]["final_state"] = container_evidence(name)["state"]
    if len(records) != 387:
        raise ValueError("Incomplete 387-status matrix")
    write_csv(artifacts / "records.csv", records)
    hot = []
    for dbms in DBMS:
        for query_id in range(1, 44):
            selected = [r for r in records if r["dbms"] == dbms and r["query_id"] == query_id]
            repeats = [r for r in selected if r["run_no"] in (2, 3)]
            valid = len(repeats) == 2 and all(r["status"] == "success" and
                                             r["validation_status"] == "passed" for r in repeats)
            hot.append({"dbms": dbms, "query_id": query_id,
                        "hot_elapsed_s": min(r["elapsed_s"] for r in repeats) if valid else None,
                        "valid": valid})
    write_csv(artifacts / "hot.csv", hot)
    metadata.update(status="complete-with-recorded-failures" if any(r["status"] != "success"
                                                                  for r in records) else "complete",
                    status_records=387, successful_records=sum(r["status"] == "success" for r in records),
                    validated_queries=sum(r["validation_status"] == "passed" for r in coverage),
                    finished_at=datetime.now(timezone.utc).isoformat())
    metadata["original_container_after"] = container_evidence("clickhouse-repro")
    if metadata["original_container_after"] != original_container:
        raise ValueError("Original ClickHouse container evidence changed during experiment")
    execute([sys.executable, "-B", str(project / "scripts" / "plot_databases.py"),
             str(artifacts)], suite / "plot.log")
    metadata["artifact_sha256"] = {
        str(path.relative_to(artifacts)): sha256(path)
        for path in sorted(artifacts.rglob("*")) if path.is_file() and path.name != "suite.json"
    }
    write_json(artifacts / "suite.json", metadata)
    execute([sys.executable, "-B", str(project / "scripts" / "validate_databases.py"),
             str(artifacts)], suite / "validation.log")
    destination = project / "results" / "databases" / suite_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(artifacts, destination)
    execute([sys.executable, "-B", str(project / "scripts" / "validate_databases.py"),
             str(destination)], suite / "published-validation.log")
    print(f"E2/E3 FINISHED: {destination}\n"
          f"Validated queries {metadata['validated_queries']}/43; "
          f"successful records {metadata['successful_records']}/387\n"
          "New experiment containers stopped; original container and volumes unchanged.", flush=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work-root", type=Path, default=Path.home() / "clickhouse-repro-offline" / "db-suites")
    parser.add_argument("--e1", type=Path, required=True,
                        help="Verified E1 Linux work directory containing data and executable")
    parser.add_argument("--prepared-data", type=Path,
                        help="Explicit checksum-verified cache from a previous data preparation")
    parser.add_argument("--reuse-databases", type=Path,
                        help="Completed Linux suite with stopped owned containers; revalidate data before new timing")
    args = parser.parse_args()
    run(args.project.resolve(), args.work_root, args.e1, args.prepared_data, args.reuse_databases)


if __name__ == "__main__":
    main()
