# 本地数据

大型 TSV、Parquet、列文件和数据库卷不进入 Git。小型可提交输入位于
`mini-index/examples/`，实验配置位于 `configs/`。

E1 正式 runner 在 WSL 的 Linux 文件系统中创建独立工作目录，默认
`~/clickhouse-repro-offline/<suite-id>/`；不要把 Windows 本地构建的耗时
与该协议的正式实验混为一组。
