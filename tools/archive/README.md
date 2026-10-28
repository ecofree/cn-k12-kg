# 历史作者代码档案

这些 `.py.txt` 文件保存应用题改为声明式 JSON 之前的作者代码，用于追溯题号、原题文和历史事故，不参与导入或发布构建。

当前应用题作者源是 `data/question_sources/application.json`；每道题的 ID 为明确字段，不再由模块顺序产生。`tools/app_items/` 只提供读取该 JSON 的兼容视图。

编辑当前材料后运行 `python tools/build_project.py --release`。完整说明见 `docs/authoring-workflow.md`。
