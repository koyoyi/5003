# Minimal columnar database skeleton

`mini_db` adds a small catalog, a deliberately restricted SQL-like parser,
scan planning and EXPLAIN around the existing `Store`. It is an educational
single-process database prototype, not a ClickHouse-compatible SQL server.
The existing `mini_index` CLI, storage format and E1 benchmark remain usable.
Frozen E1/E2/E3 artifacts are not changed by this extension.

## Build and demonstrate

Run from this project in WSL, using a fresh build directory for repeat tests:

```sh
cmake -S mini-index -B mini-index/build-db -DCMAKE_BUILD_TYPE=Release
cmake --build mini-index/build-db -j 2
ctest --test-dir mini-index/build-db --output-on-failure

printf 'row_id\tkey\tvalue\n3\t7\t3\n1\t7\t2\n2\t9\t10\n0\t-1\t-4\n' > /tmp/mini-db-input.tsv
mini-index/build-db/mini_db /tmp/mini-db-demo --execute 'CREATE TABLE demo;'
mini-index/build-db/mini_db /tmp/mini-db-demo --execute \
  "IMPORT demo FROM '/tmp/mini-db-input.tsv' GRANULE 2;"
mini-index/build-db/mini_db /tmp/mini-db-demo --execute 'SHOW TABLES;'
mini-index/build-db/mini_db /tmp/mini-db-demo --execute 'DESCRIBE demo;'
mini-index/build-db/mini_db /tmp/mini-db-demo --execute \
  'EXPLAIN SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9;'
mini-index/build-db/mini_db /tmp/mini-db-demo --execute \
  'SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9;'
mini-index/build-db/mini_db /tmp/mini-db-demo --execute \
  'SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9 USING FULL;'
```

Choose unused input/database paths when running the example. Each invocation
is a fresh process: querying does not rebuild the imported table.
Both SELECTs return count 1 and sum 10. INDEX scans 2 rows (one candidate
granule); FULL scans 4. EXPLAIN reports the same 2 candidate rows without
executing the query.

Alternatively, from the project root, run the checked-in example against a
nonexistent catalog path:

```sh
mini-index/build-db/mini_db /tmp/mini-db-script-demo --file mini-index/examples/demo.sql
```

The script imports [the small TSV fixture](examples/demo.tsv) and prints the
catalog, schema, plan and both query results. Its relative path assumes the
project root as the current working directory.

## Exact supported grammar

```text
CREATE TABLE name
SHOW TABLES
DESCRIBE name
IMPORT name FROM 'path' GRANULE positive_uint64
[EXPLAIN] SELECT COUNT(*), SUM(value) FROM name
  WHERE key = int64 [USING INDEX|FULL]
[EXPLAIN] SELECT COUNT(*), SUM(value) FROM name
  WHERE key >= int64 AND key < int64 [USING INDEX|FULL]
```

Keywords and column names are case-insensitive. Table names are case-sensitive
ASCII identifiers matching `[A-Za-z_][A-Za-z0-9_]*`; names are not paths.
Every table has the fixed schema `row_id UInt64, key Int64, value Int64`.
Both aggregates must appear, in the shown order. No aliases, SELECT *,
SQL comments, DROP, INSERT, UPDATE, JOIN or general expressions are supported.
Unsupported syntax and out-of-range integers are explicit errors.
Ranges are half-open; equality supports both signed 64-bit endpoints.
Default access is INDEX, with no cost-based optimization.

The optional final semicolon is accepted by `--execute`, which requires
exactly one statement. `--file SCRIPT.sql` or standard input accepts batches
separated by unquoted semicolons, including multiline statements. A single
quote inside an import path is escaped as `''`; backslashes are literal.
Relative import paths resolve from the process working directory, not the
catalog or script directory. There is no interactive prompt.

All statements in a batch are syntax-checked before execution. Runtime errors
stop the batch, but prior successful statements are not rolled back. Output
is one JSON object per successful command. Errors print `FAIL: ...` to stderr
and exit nonzero; an error never substitutes a successful empty result.

## Catalog, import and execution

```text
ROOT/catalog.txt             database format/version
ROOT/name/schema.txt        fixed table schema/version
ROOT/name/data/              existing immutable Store format
```

A nonexistent ROOT is initialized automatically; an existing directory must
have valid catalog metadata. CREATE publishes metadata by renaming a
`.create-name` staging directory. A newly created table is not queryable.
IMPORT accepts the same TSV header, integer types and unique row IDs as
`mini_index build`. Both LF and Windows CRLF TSV line endings are accepted.
It sorts by `(key,row_id)`, builds a single immutable
dataset in `name/.import`, reopens it for format validation, then renames it
to `name/data`. Only this published directory is queryable. Empty imports
are valid ready tables. A second import is rejected, not appended or replaced.

Failed I/O can leave staging directories. They are never treated as ready
tables, and a repeated operation reports an unfinished operation explicitly.
There is no automatic recovery or removal: inspect the named staging
directory before removing only that directory manually. Renames protect
publication visibility; there is no fsync/crash-durability guarantee.

SELECT and EXPLAIN share `Store::plan`, so candidate rows/granules come from
the actual sparse mark lookup, not a parallel approximation. The existing
4096-row buffered loop reads all three columns, filters and performs checked
Int64 aggregation. EXPLAIN's estimated row count is the candidate scan size,
not the matched result count. Requested bytes are rows times 24, not physical
I/O or projected-column accounting. SELECT's elapsed time is the Store query
time, excluding process startup, catalog reading and opening/index validation.
Stores are reopened per statement; this entry point is not a benchmark client.

## Tests and boundaries

CTest includes separate create/reload processes, 404 predicates tested against
an independent input-row oracle with full/index SQL and EXPLAIN parity, sparse
pruning, duplicate-key granules, empty tables/ranges, signed endpoints, schema
corruption, aggregate overflow and invalid/unfinished import rejection.
The CLI test exercises fresh-process commands, script/stdin input, exact
expected aggregates and scan sizes, and syntax-invalid batch non-publication.
The original persistence/correctness and invalid-input tests still run.

This skeleton demonstrates a ClickHouse-inspired sorted column store,
granule-level sparse primary-index planning and filtered aggregation.
It does not reproduce MergeTree multi-part writes/merges, compression,
column projection, vectorized expression operators, parallelism, transactions,
concurrency, a network protocol or ClickHouse SQL compatibility. Only one
process may access a catalog at a time; no lock manager is provided.
