CREATE TABLE demo;
IMPORT demo FROM 'mini-index/examples/demo.tsv' GRANULE 2;
SHOW TABLES;
DESCRIBE demo;
EXPLAIN SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9;
SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9;
SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9 USING FULL;
