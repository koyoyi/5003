# 可复现性记录

- `project-import.json`：本次从原目录复制的完整文件清单、原始 SHA-256 和字节数。
  记录的是导入时的基线；后续 CMake 和说明文档整理允许产生明确列出的差异。
- `project-organization.json`：导入后检查，包括核心源码是否一致、哪些文件经过工程整理。
- `database-preparation.json`：原工程保留的数据库准备记录。

`results/` 中的实验时间、绝对路径、源码指纹和环境信息属于历史记录，原样保留。
本轮新构建的日志与汇总位于 `build/project-check/`，不覆盖历史实验。
