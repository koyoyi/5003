# ClickHouse lightweight reproduction

Active implementation: [mini-index](mini-index/README.md).

The [minimal database skeleton](mini-index/DATABASE.md) provides the `mini_db`
entry point: fixed-schema tables, persistent catalog, one-shot import,
restricted COUNT/SUM queries and shared-plan EXPLAIN. It reuses the existing
storage/index engine and does not change the published E1/E2/E3 results.
See [the skeleton verification record](results/minimal-database/20261009-RdkwYD/verification.json)
and [the executed SQL example output](results/minimal-database/20261009-RdkwYD/demo-final.jsonl).

This project follows the v2 sparse-index plan, not the older 100M-row schedule.
The copied `ClickHouse_复现任务书_v2_稀疏索引_小规模ClickBench.md` in this
directory is the scope reference; existing notes and older plans are retained.

## Offline E1

In WSL, from this project directory:

```sh
python3 -B -m unittest discover -s scripts -p test_mechanism.py -v
python3 -B scripts/run_mechanism.py
```

Prerequisites: local Python 3.10+, C++17 compiler, CMake 3.16+, and make.
The runner does not install anything or access the network.
It stages source/build/data under `~/clickhouse-repro-offline` and publishes
verified logs, CSV summaries, SVG figures and provenance under
`results/mechanism/<unique-suite-id>`.

Scope: a usable immutable column store and sparse primary-index component,
complete-row-ID correctness tests, failure tests and the full E1 matrix.
E2 native ClickHouse and E3 small ClickBench comparisons are not implemented
by the E1 runner. It never touches database containers or their volumes.

## E2 and E3

See [the database experiment guide](scripts/DATABASES.md) for the isolated
native-index validation and deterministic adapted three-system ClickBench
runner. These stages have separate prerequisites and results; they do not
reuse or modify the existing database container/volume.

See [the component guide](mini-index/README.md) for the standalone CLI,
measurement protocol, limitations, and figure regeneration.

## Verified experiment artifacts

- [E1 summary](results/mechanism/20261009T112101Z-178093fc/summary.csv):
  all 24 configurations, 240 measurements and 48 warmups.
- [E2/E3 metadata](results/databases/20261009T120932Z-b23db388/suite.json):
  four native queries and all 43 cross-system queries validated;
  all 387 timed states succeeded on the million-row shard.
- [E3 all-query figure](results/databases/20261009T120932Z-b23db388/figures/clickbench-hot.svg)
  and [E2 native read rows](results/databases/20261009T120932Z-b23db388/figures/native-read-rows.svg).
- [The earlier failed-validation suite](results/databases/20261009T115341Z-55db29ea/query-validation.csv)
  remains available: 36/43 queries validated, 324/387 successful states.
  Invalid main times are null and do not contribute to performance summaries.

The completed E3 results are the approved deterministic adapted workload,
not an unmodified official run or a reproduction of the original 100M scale.
All three native timeout probes passed; all experiment containers are stopped.
The original ClickHouse container's before/after evidence remains identical.

Check a completed suite without rerunning measurements:

```sh
python3 -B scripts/validate_results.py results/mechanism/SUITE_ID
```

## Third-party workload

Copied and adapted ClickBench SQL uses the upstream CC BY-NC-SA 4.0 terms.
See [the attribution notice](sql/CLICKBENCH-NOTICE.txt) and the upstream license
included with each database suite's SQL. This course reproduction is
non-commercial; do not present adapted workloads as original project code.

## Course repository

This directory is the local course repository. It contains:

| Path | Purpose |
| --- | --- |
| `mini-index/` | C++17 storage/index engine, minimal database, tests and small demo |
| `configs/` | Frozen experiment settings and limited ClickHouse configuration |
| `scripts/` | Data preparation, experiment runners, regression tests and artifact validators |
| `sql/` | Native validation DDL and third-party workload attribution |
| `results/` | Verified experiment records, figures, provenance and retained failed attempts |
| `reproduction-info/` | Preparation history and experiment environment evidence |

The copied v2 task specification records the agreed working scope.
Local reference PDFs, personal notes, editor settings, build products, large
datasets, database volumes and credential files are not repository content.
They are not deleted. Published result bytes are exempt from Git line-ending
conversion so SHA-256 artifact checks remain meaningful after cloning.

Quick local verification from the project directory in WSL:

```sh
cmake -S mini-index -B mini-index/build-course -DCMAKE_BUILD_TYPE=Release
cmake --build mini-index/build-course -j 2
ctest --test-dir mini-index/build-course --output-on-failure
python3 -B scripts/test_cli.py mini-index/build-course/mini_index
python3 -B -m unittest discover -s scripts -p test_mechanism.py -v
```

Use a fresh build directory for repeated CTest runs: fixture creation refuses
to overwrite existing data. For full experiment prerequisites and commands,
see the component and database experiment guides linked above.

Repository initialization alone is not a completed course submission.
Before publication, confirm the teacher's scope requirements, prepare the
report/video and contribution statement, review publishable metadata, decide
licensing for independently written code, and configure a GitHub remote.
The ClickBench workload retains its own license and attribution; do not apply
a new project-wide license over it. No GitHub publication is implied by the
presence of local Git metadata.
