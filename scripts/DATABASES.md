# E2 and E3 isolated experiments

## Preparation and execution

The approved experiment uses three **new** containers, each limited to 2 CPUs
and 4 GiB memory, with localhost-only ports 18123/15432/13306. Existing
`clickhouse-repro` and its volumes are not started, modified or deleted.
Containers are uniquely named, run one at a time, and are stopped afterwards.
New database volumes remain available for inspection; no cleanup is automatic.

Dependencies are pinned in `requirements-databases.txt`. From WSL in the
project directory, once dependencies/images are locally available:

```sh
~/clickhouse-repro-offline/db-venv/bin/python -B \
  -m unittest discover -s scripts -p test_databases.py -v
~/clickhouse-repro-offline/db-venv/bin/python -B scripts/run_databases.py \
  --e1 ~/clickhouse-repro-offline/20261009T112101Z-178093fc
```

Preparation (venv/driver installation and image pulls) requires network access.
Experiment scripts never download workloads, images or datasets. They refuse
dirty/mismatched ClickBench checkouts and non-Linux work directories.

Config: `configs/clickbench.json`. The frozen upstream commit is recorded as
actually found locally; it is **not** represented as the paper's exact 2024
benchmark revision. Images are ClickHouse 24.6.1.4423, PostgreSQL 16.6 and
MySQL 8.0.40; actual versions/digests are captured.

ClickHouse's background scheduling pools are explicitly limited by
`configs/clickhouse-limited.xml`: the stock 512-thread scheduler could not
start within the experimental PID budget. Query threads remain limited to
two. This configuration is saved with each suite.

## E2

Uses exactly the verified E1 million-row input and checks its SHA-256 before
importing an isolated `mechanism.rows` table. Storage is MergeTree with
`PRIMARY KEY key`, `ORDER BY (key,row_id)`, G=8192 and adaptive byte granularity
disabled. Preparation merges parts before collecting evidence.

Narrow, medium and near-full key ranges are compared against both C++ methods
and an independent record-consuming oracle. A non-key value predicate is
checked against the oracle. All four compare full row_id lists and count/sum.
Outputs include DDL, queries, `EXPLAIN indexes = 1`, active part metadata and
`system.query_log` QueryFinish read_rows/read_bytes/query_duration_ms.
Native server timing is not compared to C++ absolute timing.

## E3 data integrity

The existing official `hits_0.parquet` is a fixed single shard, not a random
sample. Its actual metadata must show 1,000,000 rows and all 105 columns.
Manifest records source hash/size/schema, modification time and URL. Its
original acquisition date is unknown and is not fabricated.

Conversion decodes UTF-8 strictly, converts epoch days and epoch seconds to
dates/UTC timestamps, validates declared integer/string/timestamp bounds,
and rejects NULL in the frozen NOT NULL schema. No default replacement,
truncation or dropped columns are permitted. Embedded NUL is rejected because
PostgreSQL TEXT cannot represent it.

ClickHouse insertion explicitly attaches UTC timezone information before
calling the driver. An initial probe exposed an eight-hour shift when the
driver interpreted naive UTC timestamps using WSL's +08:00 local timezone;
that failed input never entered benchmark conclusions. Preparation failures
are documented in `reproduction-info/database-preparation.json`.

A single canonical Parquet/quoted tab-separated CSV representation is shared
across systems. Quoting preserves quotes, newlines, tabs, backslashes and
empty strings. Each system first imports the first 100,000 rows into a
separate `hits_probe`, then the complete shard into `hits`.
Every row and column is read back. Row count plus the sum and XOR of SHA-256
row hashes verify an order-independent full-data fingerprint, including
duplicates. MySQL LOAD DATA warnings cause explicit failure.

## SQL and correctness

All 43 queries are retained. Both original and adapted SQL plus SHA-256 and
reasons are saved. This is an explicitly approved **deterministic adapted
variant**, not an unmodified official scoreboard run.

The upstream repository license is CC BY-NC-SA 4.0. Each suite includes its
complete license, attribution and change notices in the SQL directory.
Adapted workload SQL retains these terms; this is a non-commercial academic
reproduction. This does not declare a blanket license for independent C++ code.

Changes include secondary tie ordering, deterministic ordering for Q18's
otherwise unspecified LIMIT, character rather than UTF-8 byte lengths,
regular-expression/backreference spelling, and fixing official MySQL Q43's
hour truncation to minute truncation. ClickHouse's newer unsupported
`auto_statistics_types` DDL setting is removed for 24.6. No extra index,
projection, skip index or preaggregation is added.

The first complete suite had 387 states but only 36/43 queries validated.
Version 2 preserves that failed suite and fixes observed semantic differences:
Q4 casts UserID to Float64 in ClickHouse to avoid its 24.6 Int64 AVG accumulator
overflow; Q22/Q23 receive complete tie handling; Q29 explicitly enables
dot-all matching in every engine. MySQL `div_precision_increment=12` preserves
AVG precision rather than weakening the frozen comparison tolerance.

PostgreSQL uses C locale, MySQL uses utf8mb4_0900_bin (case-sensitive, NO PAD),
and timezone is UTC. ClickHouse COUNT DISTINCT is explicitly uniqExact.
Integer values are exact. Floating averages are compared with pre-frozen
relative tolerance 1e-9 and absolute tolerance 1e-6. Results remain ordered
and all columns are compared; NULL is not equivalent to an empty string.

Validation queries are run for every system before formal timing. Queries
without a valid three-system reference cannot contribute performance values.

## Timing, failures and outputs

One persistent driver connection is established before timing each database.
Q1 Run1/Run2/Run3 precedes Q2 and so on, with no concurrent database work.
Main time includes request, full result receipt/decoding and shared value
normalization; it excludes docker exec, connection startup and log writing.
Server time, when available, is separate. ClickHouse result cache is disabled;
PostgreSQL/MySQL versions have no SQL query-result cache.

OS/data caches are not cleared. Validation and integrity queries precede
timing, so `first_run` is emphatically **not cold**. Hot summary is
min(Run2,Run3) only when both succeeded and passed validation.

All systems use native 60-second statement limits, with targeted cancellation
on driver errors. Error, timeout and validation-failed main elapsed fields
are null, while observed time, error and cancellation evidence are saved.
A JSONL journal flushes each status immediately; completed runs contain
exactly 3 * 43 * 3 = 387 states.

Before timing, each engine also exercises a one-second native timeout using
a deliberately long query, verifies an observed 0.5--5 second response,
issues targeted cancellation and checks that no matching query remains.
The production session limits are then restored to 60 seconds.

Artifacts are published under `results/databases/<suite_id>` with raw decoded
results, SQL adaptations, data/import fingerprints, resource/driver metadata,
E2 evidence, status CSV/JSONL and hot summary. Passwords are generated locally
in Linux-only mode-0600 env files and never copied to project results.
Each rerun creates a new suite; it does not overwrite earlier results.
Incomplete setup/import failures stop the active new container and leave
explicit exception/log evidence; they are not reported as completed suites.

For a new suite after SQL-only corrections, explicitly reuse a completed
suite's owned, stopped experiment containers and verified converted data:

```sh
~/clickhouse-repro-offline/db-venv/bin/python -B scripts/run_databases.py \
  --e1 ~/clickhouse-repro-offline/20261009T112101Z-178093fc \
  --prepared-data /absolute/Linux/path/to/prepared/data \
  --reuse-databases /absolute/Linux/path/to/completed/suite
```

This still creates a new source snapshot, suite ID and result journal, rechecks
every imported row/column, reruns native/cross-system validation and starts all
387 timing states from scratch. It never resumes a partial timing sequence or
overwrites the previous suite.

Validate and regenerate completed results without database access:

```sh
~/clickhouse-repro-offline/db-venv/bin/python -B scripts/validate_databases.py \
  results/databases/SUITE_ID
~/clickhouse-repro-offline/db-venv/bin/python -B scripts/plot_databases.py \
  results/databases/SUITE_ID
```
