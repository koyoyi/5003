"""Dependency-free, offline E1 runner. Execute with WSL Python on Linux."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

METHODS = ("full_scan", "sparse_index")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.write("\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No records for {path}")
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def config_from(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "generator_version": 1, "seed": 20260930,
        "dataset_rows": [100000, 1000000], "granule_rows": [512, 2048, 8192],
        "target_match_ratios": ["0.001", "0.01", "0.1", "1"],
        "warmup_runs": 1, "measurement_runs": 5,
        "value_min": -100, "value_max": 100,
        "key_distribution": "four-rows-per-integer-key; shuffled-input",
    }
    for key, expected in required.items():
        if key not in config or config[key] != expected:
            raise ValueError(f"E1 requires {key}={expected!r}")
    for key in ("cache_protocol", "timing_scope"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError(f"Missing {key}")
    return config


def frozen_queries(n: int, ratios: list[str]) -> list[dict[str, Any]]:
    domain = n // 4
    queries = []
    for query_id, ratio in enumerate(ratios, 1):
        width = max(1, int(Decimal(ratio) * domain))
        lower = 0 if width == domain else (domain - width) // 2
        queries.append({
            "query_id": query_id, "target_match_ratio": ratio,
            "lower_bound": lower, "upper_bound": lower + width,
            "expected_count": 0, "expected_sum": 0,
        })
    return queries


def prepare_data(path: Path, n: int, config: dict[str, Any]) -> list[dict[str, Any]]:
    queries = frozen_queries(n, config["target_match_ratios"])
    random_source = random.Random(config["seed"])
    ids = list(range(n))
    random_source.shuffle(ids)
    with path.open("x", encoding="ascii", newline="\n") as output:
        output.write("row_id\tkey\tvalue\n")
        for row_id in ids:
            key = row_id // 4
            value = random_source.randint(config["value_min"], config["value_max"])
            output.write(f"{row_id}\t{key}\t{value}\n")
            # This oracle consumes each generated record, not an aggregate formula.
            for query in queries:
                if query["lower_bound"] <= key < query["upper_bound"]:
                    query["expected_count"] += 1
                    query["expected_sum"] += value
    return queries


def execute(command: list[str], log: Path, timeout: int = 600) -> str:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, check=False)
    log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}\n{result.stdout}")
    return result.stdout


def validate_samples(
    samples: list[dict[str, str]], n: int, granule: int, queries: list[dict[str, Any]],
) -> None:
    if len(samples) != 48:
        raise ValueError(f"Expected 48 samples, got {len(samples)}")
    expected_keys = {(q["query_id"], method, run)
                     for q in queries for method in METHODS for run in range(6)}
    seen: set[tuple[int, str, int]] = set()
    query_map = {q["query_id"]: q for q in queries}
    for record in samples:
        key = (int(record["query_id"]), record["method"], int(record["run_no"]))
        if key not in expected_keys or key in seen:
            raise ValueError(f"Unexpected/duplicate sample {key}")
        seen.add(key)
        query = query_map[key[0]]
        count = int(record["result_count"])
        scanned = int(record["scanned_rows"])
        elapsed = float(record["elapsed_s"])
        if not math.isfinite(elapsed) or elapsed <= 0:
            raise ValueError("Invalid elapsed time")
        if record["validation_status"] != "passed":
            raise ValueError("Sample failed validation")
        if record["phase"] != ("warmup" if key[2] == 0 else "measurement"):
            raise ValueError("Incorrect measurement phase")
        if (int(record["lower_bound"]) != query["lower_bound"] or
                int(record["upper_bound"]) != query["upper_bound"]):
            raise ValueError("Query bounds changed")
        if (count != query["expected_count"] or
                int(record["result_sum"]) != query["expected_sum"] or
                int(record["matched_rows"]) != count):
            raise ValueError("Generated-data oracle mismatch")
        if (not count <= scanned <= n or
                int(record["requested_read_bytes"]) != scanned * 24 or
                int(record["index_bytes"]) != ((n + granule - 1) // granule) * 24):
            raise ValueError("Invalid scan/space accounting")
        if key[1] == "full_scan" and scanned != n:
            raise ValueError("Full scan did not scan every row")
    if seen != expected_keys:
        raise ValueError("Missing samples")


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for row in records:
        key = (int(row["dataset_rows"]), int(row["granule_rows"]), int(row["query_id"]))
        groups.setdefault(key, []).append(row)
    if len(groups) != 24:
        raise ValueError(f"Expected 24 E1 configurations, got {len(groups)}")
    summary: list[dict[str, Any]] = []
    for (n, granule, query_id), rows in sorted(groups.items()):
        method_stats: dict[str, dict[str, float]] = {}
        for method in METHODS:
            measured = [r for r in rows if r["method"] == method and r["phase"] == "measurement"]
            if (len(measured) != 5 or {int(r["run_no"]) for r in measured} != set(range(1, 6))
                    or any(r["validation_status"] != "passed" for r in measured)):
                raise ValueError("Summary requires five validated measurements per method")
            times = [float(r["elapsed_s"]) for r in measured]
            if any(not math.isfinite(t) or t <= 0 for t in times):
                raise ValueError("Invalid measured times")
            method_stats[method] = {
                "median": statistics.median(times), "min": min(times), "max": max(times),
            }
        indexed = next(r for r in rows if r["method"] == "sparse_index")
        summary.append({
            "dataset_rows": n, "granule_rows": granule, "query_id": query_id,
            "target_match_ratio": indexed["target_match_ratio"],
            "actual_match_ratio": indexed["actual_match_ratio"],
            "scanned_rows": int(indexed["scanned_rows"]),
            "scan_ratio": int(indexed["scanned_rows"]) / n,
            "index_bytes": int(indexed["index_bytes"]),
            "speedup": method_stats["full_scan"]["median"] / method_stats["sparse_index"]["median"],
            **{f"{method}_{name}_s": value
               for method, stats in method_stats.items() for name, value in stats.items()},
        })
    return summary


def run(project: Path, work_root: Path, config_path: Path) -> Path:
    if sys.platform != "linux" or work_root.resolve().is_relative_to(Path("/mnt")):
        raise ValueError("Run with WSL Python and a Linux-filesystem work root, not /mnt")
    config = config_from(config_path)
    suite_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    suite = work_root / suite_id
    suite.mkdir(parents=True, exist_ok=False)
    snapshot = suite / "source"
    source_paths = [
        project / "mini-index" / "CMakeLists.txt",
        project / "mini-index" / "README.md",
        *sorted((project / "mini-index" / "src").glob("*")),
        *sorted((project / "mini-index" / "tests").glob("*")),
        *sorted((project / "scripts").glob("*.py")),
    ]
    source_hashes = {}
    for source in source_paths:
        relative = source.relative_to(project)
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hashes[str(relative)] = sha256(target)
    source_digest = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    code_commit = f"unversioned:source_sha256:{source_digest}"
    write_json(suite / "config.json", config)
    (snapshot / "configs").mkdir()
    write_json(snapshot / "configs" / "mechanism.json", config)
    build = suite / "build"
    artifacts = suite / "artifacts"
    artifacts.mkdir()
    shutil.copy2(suite / "config.json", artifacts / "config.json")
    metadata: dict[str, Any] = {
        "suite_id": suite_id, "status": "running", "code_commit": code_commit,
        "source_sha256": source_digest, "source_files": source_hashes,
        "config_sha256": sha256(suite / "config.json"),
        "python": sys.version, "platform": platform.platform(),
        "cpu_count": os.cpu_count(), "cpu": Path("/proc/cpuinfo").read_text(),
        "memory": Path("/proc/meminfo").read_text(),
        "filesystem_free_bytes": shutil.disk_usage(suite).free,
        "work_path": str(suite), "datasets": [],
        "cache_protocol": config["cache_protocol"], "timing_scope": config["timing_scope"],
        "limitations": [
            "Single-threaded simplified immutable store, not ClickHouse format.",
            "Warm page-cache protocol, not physical disk I/O or strict cold timing.",
            "Build timings exclude input parsing, duplicate-ID validation and OS fsync.",
            "Snapshot fingerprint substitutes for code_commit and includes uncommitted source changes.",
        ],
    }
    write_json(artifacts / "suite.json", metadata)
    execute(["cmake", "-S", str(snapshot / "mini-index"), "-B", str(build),
             "-DCMAKE_BUILD_TYPE=Release"], artifacts / "configure.log")
    execute(["cmake", "--build", str(build), "-j", "2"], artifacts / "build.log")
    execute(["ctest", "--test-dir", str(build), "--verbose"], artifacts / "tests.log")
    execute([sys.executable, "-B", "-m", "unittest", "discover", "-s",
             str(snapshot / "scripts"), "-p", "test_mechanism.py", "-v"],
            artifacts / "runner-tests.log")
    execute([sys.executable, "-B", str(snapshot / "scripts" / "test_cli.py"),
             str(build / "mini_index")], artifacts / "cli-tests.log")
    shutil.copy2(build / "fixtures" / "correctness.csv", artifacts / "correctness.csv")
    compiler = execute(["c++", "--version"], artifacts / "compiler.log").splitlines()[0]
    flags = (build / "CMakeFiles" / "mini_index.dir" / "flags.make").read_text()
    metadata.update(compiler=compiler, compiler_flags=flags)
    write_json(artifacts / "suite.json", metadata)
    executable = build / "mini_index"
    all_samples: list[dict[str, Any]] = []
    builds: list[dict[str, Any]] = []
    for n in config["dataset_rows"]:
        dataset_id = f"A-v1-seed{config['seed']}-N{n}"
        data_path = suite / f"{dataset_id}.tsv"
        queries = prepare_data(data_path, n, config)
        dataset_hash = sha256(data_path)
        query_path = suite / f"queries-{n}.tsv"
        with query_path.open("x", encoding="ascii", newline="\n") as output:
            output.write("query_id\tlower\tupper\n")
            for query in queries:
                output.write(f"{query['query_id']}\t{query['lower_bound']}\t{query['upper_bound']}\n")
        write_json(artifacts / f"queries-{n}.json", queries)
        metadata["datasets"].append({
            "dataset_id": dataset_id, "rows": n, "input_sha256": dataset_hash,
            "input_bytes": data_path.stat().st_size, "generator_version": 1,
            "seed": config["seed"], "frozen_query_sha256": sha256(query_path),
        })
        shutil.copy2(query_path, artifacts / query_path.name)
        write_json(artifacts / "suite.json", metadata)
        for granule in config["granule_rows"]:
            label = f"N{n}-G{granule}"
            store = suite / label
            stats = json.loads(execute(
                [str(executable), "build", str(data_path), str(store), str(granule)],
                artifacts / f"{label}-build.log"))
            column_hashes = {name: sha256(store / name)
                             for name in ("row_id.bin", "key.bin", "value.bin")}
            previous = [r for r in builds if r["dataset_rows"] == n]
            if previous and previous[0]["column_sha256"] != json.dumps(column_hashes, sort_keys=True):
                raise ValueError("Granule configurations did not reuse identical column data")
            builds.append({
                "dataset_rows": n, "granule_rows": granule, **stats,
                "data_bytes": n * 24, "index_bytes": (store / "index.bin").stat().st_size,
                "column_sha256": json.dumps(column_hashes, sort_keys=True),
                "index_sha256": sha256(store / "index.bin"),
            })
            raw_path = artifacts / f"{label}-samples.csv"
            execute([str(executable), "benchmark", str(store), str(query_path), str(raw_path)],
                    artifacts / f"{label}-benchmark.log")
            samples = read_csv(raw_path)
            validate_samples(samples, n, granule, queries)
            for sample in samples:
                query = queries[int(sample["query_id"]) - 1]
                all_samples.append({
                    "suite_id": suite_id, "dataset_id": dataset_id,
                    "dataset_sha256": dataset_hash, "dataset_rows": n,
                    "granule_rows": granule, **sample,
                    "target_match_ratio": query["target_match_ratio"],
                    "actual_match_ratio": int(sample["matched_rows"]) / n,
                    "code_commit": code_commit, "compiler": compiler, "compiler_flags": flags,
                    "cache_protocol": config["cache_protocol"], "timing_scope": config["timing_scope"],
                    "log_path": f"{label}-samples.csv",
                })
            print(f"Validated {label}: 4 queries, 8 warmups, 40 measurements", flush=True)
    if len(all_samples) != 288:
        raise ValueError("Incomplete E1 sample matrix")
    summary = summarize(all_samples)
    write_csv(artifacts / "samples.csv", all_samples)
    write_csv(artifacts / "summary.csv", summary)
    write_csv(artifacts / "build-costs.csv", builds)
    execute([sys.executable, str(snapshot / "scripts" / "plot_results.py"),
             str(artifacts / "summary.csv"), str(artifacts / "figures")],
            artifacts / "plots.log")
    metadata.update(status="complete", configurations=len(summary),
                    measurement_records=240, warmup_records=48,
                    finished_at=datetime.now(timezone.utc).isoformat())
    metadata["artifact_sha256"] = {
        str(path.relative_to(artifacts)): sha256(path)
        for path in sorted(artifacts.rglob("*")) if path.is_file() and path.name != "suite.json"
    }
    write_json(artifacts / "suite.json", metadata)
    execute([sys.executable, "-B", str(snapshot / "scripts" / "validate_results.py"),
             str(artifacts)], suite / "artifact-validation.log")
    destination = project / "results" / "mechanism" / suite_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(artifacts, destination)
    execute([sys.executable, "-B", str(snapshot / "scripts" / "validate_results.py"),
             str(destination)], suite / "published-validation.log")
    print(f"E1 COMPLETE: {destination}\nLinux datasets and build: {suite}", flush=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--work-root", type=Path, default=Path.home() / "clickhouse-repro-offline")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    run(project, args.work_root, args.config or project / "configs" / "mechanism.json")


if __name__ == "__main__":
    main()
