CREATE DATABASE mechanism;
CREATE TABLE mechanism.rows
(
    row_id UInt64,
    key Int64,
    value Int64
)
ENGINE = MergeTree
PRIMARY KEY key
ORDER BY (key, row_id)
SETTINGS index_granularity = 8192, index_granularity_bytes = 0;
