# DSC5003 · ClickHouse 稀疏索引复现项目

这是整理后的课程工程入口：C++17 实现可持久化的列存储、稀疏主索引和最小数据库；Python 负责正确性验证、机制实验与三数据库对照。研究范围以根目录的 [v2 任务书](ClickHouse_复现任务书_v2_稀疏索引_小规模ClickBench.md) 为准。

项目从原 `Spark Hadoop ClickHouse等/5003 project` 整理到当前 `project1/5003` Git 仓库。原目录保留；已复制源码、配置及历史实验依据，未把旧构建缓存、数据库卷、个人笔记和参考 PDF 混入工程。原项目说明保存在 [README.legacy.md](README.legacy.md)。

## 目录

```text
5003/
├── CMakeLists.txt / CMakePresets.json  # 统一构建与测试入口
├── mini-index/
│   ├── src/                          # 核心组件、CLI、数据库入口
│   ├── tests/                        # 持久化、边界、随机与失败测试
│   └── examples/                     # 可运行的 TSV / SQL 示例
├── scripts/                          # 数据、实验调度、校验、绘图与回归
├── configs/                          # 冻结的实验设置
├── sql/                              # 原生验证 DDL 与工作负载署名
├── results/                          # 历史实验日志、摘要、图与来源证据
├── reproduction-info/                # 环境与整理来源记录
├── docs/                             # 组件设计与开发说明
├── report/                           # 报告、个人贡献和视频材料
├── data/                             # 本地数据，不提交大文件
└── build/                            # 本地构建输出，不提交
```

组件职责见 [架构与组件索引](docs/architecture.md)，完整操作见 [开发说明](docs/development.md)。CMake 明确提供 `mini_index_core`、`mini_index_cli` 和 `mini_database` 组件目标；`mini_index`、`mini_db` 是两个独立程序。

## 快速运行

需要 C++17 的 g++、CMake 3.21+、Ninja 和 Python 3.10+。在本目录打开终端：

```sh
cmake --preset native-release
cmake --build --preset native-release
ctest --preset native-release
python -B -m unittest discover -s scripts -p test_mechanism.py -v
python -B -m unittest discover -s scripts -p test_databases.py -v
```

上述离线组件及回归测试无需第三方 Python 库，不连接数据库。WSL/Linux 可将 `python` 换成 `python3`。调试构建使用 `native-debug` preset。

统一执行构建、测试和历史产物校验：`python -B tools/check_project.py`。检查日志位于 `build/project-check/`；也可从 VS Code 运行 `5003: verify offline` 任务。

Windows PowerShell 最小演示：

```powershell
build/native-release/mini-index/mini_index.exe build mini-index/examples/demo.tsv data/demo-store 2
build/native-release/mini-index/mini_index.exe query data/demo-store full range 7 10 ids
build/native-release/mini-index/mini_index.exe query data/demo-store index range 7 10 ids
build/native-release/mini-index/mini_db.exe data/demo-catalog --file mini-index/examples/demo.sql
python -B scripts/test_cli.py build/native-release/mini-index/mini_index.exe
```

Linux 使用相同路径并去掉 `.exe`。示例构建和导入拒绝覆盖已有数据，重复演示请换用新的数据路径。两条范围查询应返回相同的 row_id、count 和 sum；扫描行数可不同。

## 三组实验入口

| 阶段 | 研究内容 | 运行与解释 |
| --- | --- | --- |
| E1 · 自主组件 | 24 组行数、granule 大小和选择率配置；全扫描与稀疏索引对照 | [组件说明](mini-index/README.md)；WSL 中运行 `python3 -B scripts/run_mechanism.py` |
| E2 · 原生机制 | 同源数据上的 ClickHouse 查询结果、EXPLAIN 与读取统计 | [数据库实验指南](scripts/DATABASES.md) |
| E3 · 系统对照 | 固定百万行分片、三数据库、43 查询，每条三次，共 387 条状态 | [数据库实验指南](scripts/DATABASES.md)；依赖 `requirements-databases.txt` |

完整实验在 WSL 的 Linux 文件系统中创建独立工作目录，新增结果使用唯一 suite ID。E2/E3 需要预先准备数据、固定版本的 ClickBench checkout、数据库镜像和驱动；本次工程整理不自动启动这些实验。

## 已保存的实验依据

以下为原项目的历史记录，时间、运行环境和源码 fingerprint 保留原值；整理后的本地构建验证与这些历史测量分开记录。

- [E1 摘要](results/mechanism/20261009T112101Z-178093fc/summary.csv)：24 组配置、240 次正式测量与 48 次预热。
- [E2/E3 suite 记录](results/databases/20261009T120932Z-b23db388/suite.json)：保存原生验证和 387 条三库计时状态的元数据。
- [E3 逐查询图](results/databases/20261009T120932Z-b23db388/figures/clickbench-hot.svg) 与 [E2 原生读取行数图](results/databases/20261009T120932Z-b23db388/figures/native-read-rows.svg)。
- [早期失败校验记录](results/databases/20261009T115341Z-55db29ea/query-validation.csv)：保留 SQL 语义差异与失败依据。
- [最小数据库验证记录](results/minimal-database/20261009-RdkwYD/verification.json) 和 [SQL 演示输出](results/minimal-database/20261009-RdkwYD/demo-final.jsonl)。

旧产物可用 `validate_results.py`、`validate_databases.py` 复核，不必重新测量；命令与依赖见 [开发说明](docs/development.md)。E3 是保留 SQL 原文及修改理由的确定性适配工作负载，数据和缓存条件均不同于原论文的一亿行实验。

## 后续交付

在当前源码和已有证据上完成课程报告、个人贡献说明、8–10 分钟视频及最终复跑记录；教师确认的范围和正式提交信息需按实际情况填写。独立代码的许可由项目成员确定，ClickBench SQL 继续遵守 [原工作负载署名及许可](sql/CLICKBENCH-NOTICE.txt)。
