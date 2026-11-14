# 开发、运行与验证

所有命令在整理后的 `project1/5003` 根目录执行。Windows 下使用 `python`；WSL/Linux 可替换为 `python3`。

## 本地依赖

离线组件需要 Python 3.10+、C++17 编译器和 CMake。根目录 presets 需要 CMake 3.21+、Ninja 和 PATH 中的 g++。无需安装 Python 第三方库即可构建、运行组件和 E1 单元测试。

原生 Windows 与 WSL 的构建目录应分开使用。根 `native-release`/`native-debug` presets 面向本地开发；完整性能实验使用 WSL runner 在 Linux 文件系统下创建独立工作目录。

## 构建与 C++ 测试

```sh
cmake --preset native-release
cmake --build --preset native-release
ctest --preset native-release
```

调试构建把 `native-release` 替换为 `native-debug`。Presets 固定使用 Ninja 和 g++；其他工具链可使用下方的手工构建方式，选择不同构建目录。

Ninja 输出在 `build/native-release/mini-index/`；Windows 程序名为 `mini_index.exe`、`mini_db.exe`，Linux 不带 `.exe`。手工使用 Visual Studio 等多配置生成器时，输出可能再增加 `Release/` 子目录。

六项 CTest 覆盖 Store 持久化与重载、非法输入、数据库创建与重载、数据库 CLI。测试的临时 fixture 位于当前构建目录，和 `data/`、历史 `results/` 分离。

也可以执行 `python -B tools/check_project.py`，统一构建、运行 CTest、Python/CLI 回归并校验有完整校验清单的历史结果。日志与汇总写入 `build/project-check/<检查时间>/`。VS Code 的 `5003: verify offline` 任务使用同一入口。

原组件仍可独立构建，适合 CMake 3.16+ 环境：

```sh
cmake -S mini-index -B mini-index/build-local -DCMAKE_BUILD_TYPE=Release
cmake --build mini-index/build-local --config Release -j 2
ctest --test-dir mini-index/build-local -C Release --output-on-failure
```

Windows 使用 g++/Ninja 手工构建时，在第一条命令加上 `-G Ninja -DCMAKE_CXX_COMPILER=g++`。WSL 另用 `mini-index/build-linux` 等目录，避免与原生 Windows 的 CMake 缓存共用。

## Python 与 CLI 回归测试

```sh
python -B -m unittest discover -s scripts -p test_mechanism.py -v
python -B -m unittest discover -s scripts -p test_databases.py -v
```

这两组测试不连接数据库。CLI 测试需显式传入构建后的程序路径，Windows 示例：

```powershell
python -B scripts/test_cli.py build/native-release/mini-index/mini_index.exe
```

Linux 对应命令：

```sh
python3 -B scripts/test_cli.py build/native-release/mini-index/mini_index
```

不要直接使用 `discover -p 'test_*.py'` 代替以上命令：`test_cli.py` 需要入口参数初始化可执行程序路径，`test_database_runner.py` 则需要数据库驱动依赖。

安装 E2/E3 可选依赖后，还可运行不连接真实数据库的 runner 回归：

```sh
python -m pip install -r requirements-databases.txt
python -B -m unittest discover -s scripts -p test_database_runner.py -v
```

## 最小演示

Windows PowerShell：

```powershell
build/native-release/mini-index/mini_index.exe build mini-index/examples/demo.tsv data/demo-store 2
build/native-release/mini-index/mini_index.exe query data/demo-store full range 7 10 ids
build/native-release/mini-index/mini_index.exe query data/demo-store index range 7 10 ids
build/native-release/mini-index/mini_db.exe data/demo-catalog --file mini-index/examples/demo.sql
```

Linux 使用同一路径并去掉 `.exe`。示例每次选择尚不存在的数据或 catalog 路径；组件会拒绝覆盖已有数据。两条查询应返回一致的 row_id、count 和 sum。数据库 SQL 示例中的导入路径以项目根目录为基准。

## 历史结果校验

已有日志可以在不启动数据库、不重跑测量的情况下校验：

```sh
python -B scripts/validate_results.py results/mechanism/20261009T112101Z-178093fc
python -B scripts/validate_databases.py results/databases/20261009T120932Z-b23db388
```

上述两个校验器都只依赖 Python 标准库；SQL 指纹计算已放入共享工具模块，验证旧结果无需安装数据库驱动。校验通过说明保存产物内部一致，不代表本轮重新运行过性能实验。历史记录中的 Linux 路径和源码 fingerprint 是当时的来源信息，应保留。

最早两组 E1 留存目录没有后续格式的完整校验清单，统一检查会明确跳过；这些文件仍通过 `reproduction-info/project-import.json` 对照导入原始字节。E2/E3 的早期失败 suite 可以通过产物一致性检查，但其中记录的失败状态仍是失败。

## 新实验

E1 从 WSL 运行：

```sh
python3 -B scripts/run_mechanism.py
```

runner 在 Linux 工作目录构建快照、准备数据并发布新的 `results/mechanism/<suite_id>`。它使用 `configs/mechanism.json`，不启动 Docker，也不覆盖旧 suite。源码迁移后需重新测量时创建新 suite，不能把旧结果改写成当前构建的结果。

E2/E3 的数据、固定 ClickBench checkout、镜像、驱动及 WSL 环境需另行准备。完整前提和参数见 [`scripts/DATABASES.md`](../scripts/DATABASES.md)。runner 负责隔离实验容器；项目整理本身不启动这些实验。实验数据和镜像准备与测量分开进行。

## 修改约定

- 新组件优先在 `mini-index/src/` 内扩展，并通过明确的 CMake 组件依赖接入两个入口。
- runner 按现有路径创建源码快照；移动源文件时同时检查快照清单、构建文件与文档。
- 新实验结果用唯一 suite ID；保留失败状态与校验信息。
- `results/` 与 `reproduction-info/` 的原始字节用于 SHA-256 对照，避免编辑器自动换行转换。
- 大数据、数据库卷、构建输出和本地凭据不提交；第三方 ClickBench SQL 保留原许可和署名。

课程交付仍需教师认可的范围、报告、个人贡献说明和视频材料。保存的实验结果与可构建源码只是这些交付中的一部分。
