"""Validate a published E1 suite against raw logs, protocol and SHA-256 hashes."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from run_mechanism import read_csv, sha256, summarize, validate_samples


def validate(suite: Path) -> None:
    metadata = json.loads((suite / "suite.json").read_text(encoding="utf-8"))
    if metadata["status"] != "complete":
        raise ValueError("Suite is not complete")
    if sha256(suite / "config.json") != metadata["config_sha256"]:
        raise ValueError("Config checksum mismatch")
    for name, digest in metadata["artifact_sha256"].items():
        if sha256(suite / name) != digest:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    samples = read_csv(suite / "samples.csv")
    if len(samples) != 288:
        raise ValueError("Expected 288 raw samples")
    if any(row["suite_id"] != metadata["suite_id"] or
           row["code_commit"] != metadata["code_commit"] or
           row["cache_protocol"] != metadata["cache_protocol"] or
           row["timing_scope"] != metadata["timing_scope"] for row in samples):
        raise ValueError("Sample provenance mismatch")
    for n in (100000, 1000000):
        queries = json.loads((suite / f"queries-{n}.json").read_text(encoding="utf-8"))
        for granule in (512, 2048, 8192):
            selected = [r for r in samples if int(r["dataset_rows"]) == n
                        and int(r["granule_rows"]) == granule]
            validate_samples(selected, n, granule, queries)
            raw = read_csv(suite / f"N{n}-G{granule}-samples.csv")
            if len(raw) != len(selected):
                raise ValueError("Combined/raw log length mismatch")
            for record, combined in zip(raw, selected):
                if any(combined[key] != value for key, value in record.items()):
                    raise ValueError("Combined/raw log content mismatch")
    expected = summarize(samples)
    actual = read_csv(suite / "summary.csv")
    if len(actual) != len(expected):
        raise ValueError("Summary row count mismatch")
    for calculated, stored in zip(expected, actual):
        if set(calculated) != set(stored):
            raise ValueError("Summary field mismatch")
        for key, value in calculated.items():
            if not math.isclose(float(value), float(stored[key]), rel_tol=1e-12, abs_tol=1e-15):
                raise ValueError(f"Summary mismatch: {key}")
    if len(read_csv(suite / "build-costs.csv")) != 6:
        raise ValueError("Expected six construction records")
    correctness = read_csv(suite / "correctness.csv")
    if len(correctness) != 25950 or any(r["validation_status"] != "passed" for r in correctness):
        raise ValueError("Correctness checkpoint incomplete")
    print("PASS: 24 configurations, 240 measurements, 48 warmups; "
          "raw logs, summaries, config and artifact hashes verified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    validate(parser.parse_args().suite)
