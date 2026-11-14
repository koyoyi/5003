# Lightweight sparse primary index and offline E1

This implements the v2 task specification's immutable, single-key component
and complete 24-configuration E1 runner. It is not a ClickHouse rewrite.
E2 native validation and E3 three-system ClickBench are separate completed
experiments; see the root README for verified artifacts.
The existing WSL database project and Docker volumes are unchanged.

The optional [minimal database skeleton](DATABASE.md) adds `mini_db`: persistent
table metadata, one-shot import, restricted aggregate queries and EXPLAIN.
It reuses this component rather than replacing its CLI or benchmark protocol.

## Complete offline suite

From the Windows project directory opened in WSL:

```sh
python3 -B -m unittest discover -s scripts -p test_mechanism.py -v
python3 -B scripts/run_mechanism.py
```

The runner copies a source snapshot into a uniquely named directory below
`~/clickhouse-repro-offline`, builds with Release C++17, runs correctness and
failure tests, generates frozen input/query files, and executes E1 sequentially.
All build/data/timing operations use the Linux filesystem, not `/mnt`.
Artifacts are checked against raw logs, computed summaries and SHA-256 hashes
both before and after copying back to `results/mechanism/<suite_id>`.
No downloads or third-party Python packages are needed.

The config is `configs/mechanism.json`: N=100000/1000000,
G=512/2048/8192, target selectivity=0.1%/1%/10%/100%.
Inputs have four records per integer key, bounded random values and shuffled
input order, seed 20260930, generator version 1. The generator consumes
individual records to build an independent count/sum oracle.
Intervals are frozen before building any G; column hashes must be identical
across G. Query position is central except the full-table interval.
This controlled distribution does not represent all workloads.

Each `(N,G,query)` first validates complete row_id vectors between methods,
then performs one warmup and five timed runs per method. Within each run the
methods alternate; the first method also alternates between runs.
Every measured aggregate is checked. Python additionally validates count/sum
against the generated-record oracle, sample shape, bounds and read accounting.

Timing starts before candidate lookup and includes seek, buffered reads,
filtering and checked aggregation. Open column streams and the index are
reused in a single sequential process per dataset. Startup, initial loading,
row_id collection and log output are excluded. The shared execution loop still
reads all three columns in both modes. There is no concurrent-query support.
The index and data are warmed by correctness checking; OS caches are not
cleared, and no strict-cold claim is made.

Outputs include 240 measured and 48 warmup samples, 24 summary rows,
six build-cost rows, correctness logs, six SVG figures, frozen queries and
provenance (config/source/input/column/index hashes, compiler flags and WSL
environment). The runner uses an unversioned source fingerprint for `code_commit`
so that uncommitted source changes are covered even in a Git checkout.
Historical experiment fingerprints remain unchanged. Source snapshots and large datasets remain
in the Linux work directory; experiment results are never overwritten.

Sort, column-write and index-write durations are separate and include closing
the output streams, but not input parsing, duplicate-ID checking or an OS
fsync. They are not durable-storage latency measurements.

Rebuild figures from any completed suite:

```sh
python3 -B scripts/validate_results.py results/mechanism/SUITE_ID
python3 -B scripts/plot_results.py \
  results/mechanism/SUITE_ID/summary.csv \
  results/mechanism/SUITE_ID/figures
```

## Build and test in WSL

Open WSL in the Windows project directory. With g++, CMake and make installed:

```sh
cmake -S mini-index -B mini-index/build -DCMAKE_BUILD_TYPE=Release
cmake --build mini-index/build
ctest --test-dir mini-index/build --verbose
```

These manual commands build on the current filesystem; use the suite runner
above for performance work. No network or external dependencies are used.
CTest recreates only its four generated fixture directories inside the current
build directory, so the test suite can be repeated. The component's dataset
creation commands still refuse to overwrite user datasets.

CTest starts separate `create` and `verify` processes. The latter reloads
persisted files without rebuilding them, and compares complete row_id sets,
count and sum against both full scan and an independent in-memory oracle.
Fixtures cover empty, single-row, all-equal, 1000-row duplicate-key and signed
integer endpoint cases, with granule sizes 1, 16, 512, 2048 and 8192.
Each fixture runs 200 seeded random queries plus boundary queries, including
empty ranges, absent keys, cross-granule duplicates and partial final granules.

## Storage and statistics

Each immutable dataset has three fixed-width, little-endian column files:
unsigned 64-bit row_id and signed 64-bit key/value (two's complement).
Rows are sorted by `(key, row_id)`. Each 24-byte index entry contains the first
key, group number and row offset. The manifest records format version, byte
order, field types, row count and granule size.

Range predicates are half-open `[lower, upper)`. Equality is separate so that
the largest signed key is supported without increment overflow. Both methods
use the same buffered column-reading, filtering and checked aggregation code.
Files are immutable after opening; the store is single-threaded. Reopening
validates the manifest, exact file sizes, index order/offsets and mark keys.
These checks do not detect arbitrary same-size corruption within column data;
the experiment's SHA-256 hashes provide provenance, not automatic recovery.

## Standalone commands

The `mini_index` executable is built beside the tests. Input is a TSV with
header `row_id`, `key`, `value`, separated by tabs. Each row_id must be unique.
Existing dataset directories are rejected; there is no in-place mutation.

```sh
mini-index/build/mini_index build input.tsv dataset-directory 512
mini-index/build/mini_index query dataset-directory full range 10 100 ids
mini-index/build/mini_index query dataset-directory index range 10 100 aggregate
mini-index/build/mini_index query dataset-directory index eq 42 42 ids
```

The upper argument is unused for `eq`. Output is JSON; failures print `FAIL`
to stderr and return a nonzero status. `ids` returns every matching row_id;
`aggregate` avoids allocating a result ID vector.
`benchmark STORE QUERIES.tsv OUTPUT.csv` is the runner's internal E1 command.

`build/fixtures/correctness.csv` records every checked query and both methods,
including scanned rows, logical requested bytes, index bytes and observed time.
Requested bytes do not measure physical disk I/O. These are correctness-mode
timings (including row_id collection), **not** the task
specification's warmed five-sample E1 benchmark timings or speedup evidence.
Generation uses seed 20260930 and values in [-100, 100].

Failure tests cover invalid G, duplicate IDs, overwrite attempts, reversed
ranges, malformed/truncated files, invalid mark keys, aggregate-mode parity
and signed sum overflow/underflow.

The copied v2 specification in the project root supersedes the older 100M-row
plan for this work. Native ClickHouse validation and the 387 ClickBench status
records are documented separately. No E1 result alone attributes system-level
ClickHouse performance to this mechanism.
