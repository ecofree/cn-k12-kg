# 构建、验收与交付

正式资源从登记的作者JSON构建。开发检查可以在未提交的工作树运行；正式发行必须对应一个干净提交，并绑定该提交的文件清单、核心报告、浏览器报告和原始日志。

## 日常开发

```bash
python -m pip install -r requirements-dev.txt
python tools/build_project.py --write
python tools/build_project.py --check
```

`--write`依次重建题源、登记新增稳定ID、编译catalog、检查内容、构建内容数据库，最后生成 `release-manifest.json`。`--check`检查已有来源与产物，不改作者数据。两者产生的报告写入被Git忽略的 `build/`。

完整开发验收可使用 `python tools/build_project.py --release --with-browser`。此命令可以发现功能问题，但未提交的工作树会记录 `source_clean=false`，不能用其报告制作正式发行包。

## 浏览器依赖

安装Node.js 20+，再执行：

```bash
npm ci
npx playwright install --with-deps chromium
```

`package-lock.json`固定Playwright及依赖。Linux截图建议安装中文字体，例如 `fonts-noto-cjk`。浏览器安装步骤依据 [Playwright官方CI文档](https://playwright.dev/docs/ci)；CI会安装浏览器和系统依赖，执行与本地相同的验收入口。

已有受控运行环境可以指定 `CNKG_NODE`、`CNKG_PLAYWRIGHT_MODULE`、`CNKG_CHROMIUM_EXECUTABLE`、`FONTCONFIG_FILE`。实际Node、Playwright和Chromium版本写入每条旅程结果，不把不同浏览器环境隐去。

## 正式发行顺序

1. 完成作者文件与代码修改，审查过新增目标后显式更新范围清单，再运行 `python tools/build_project.py --write`。从真实基线生成语义差异、影响及页面清单，填写逐项审查记录并由 `docs/reviews/current.json` 指向它，详见 [语义审查](semantic-change-review.md)。填写记录与文档后，最后运行 `python tools/release_manifest.py --write`，将这些实际审查文件也纳入发行清单。
2. 检查差异，将作者源、注册表、派生目录、内容清单和 `release-manifest.json` 一并提交。
3. 确认工作树干净，运行 `python tools/release_check.py --with-browser`。
4. 检查17项核心门禁与12条浏览器流程全部通过，运行 `python tools/package_release.py`。

Git历史包要求完整历史。若 `git rev-parse --is-shallow-repository` 返回 `true`，先运行 `git fetch --unshallow origin` 补齐历史；该操作不会切换分支或更改工作树。打包器拒绝用浅克隆生成一个看似完整但缺少父提交的包。

构建之后如果又修改文档、代码、Schema、字体或其他发行文件，应重新生成清单并提交，再验收。运行中改变提交、清单或源码会使报告失败。缺依赖或启动失败时不会继续沿用上一次报告作为本次通过证据。

当前17项核心门禁包括发布清单、来源一致性、稳定身份、catalog、显式题目准入、完整目标范围、语义审查绑定、图与答案结构、讲解、单元、实践、课标、既定结构覆盖、数值复算、回归、状态与数据库构建。`--with-browser`增加第18项门禁，其中运行12条浏览器旅程。

浏览器旅程验证自检保存与参考曝光、过程草稿与自评、完整单元的三阶段作答、实践修订与导出、学段选择及手机布局、题池耗尽后的复习入口。每条使用独立临时学习库与浏览器上下文。它们使用作者答案，证据类型固定为 `synthetic_browser_test`。

V3增加 `navigation_recovery` 与 `keyboard_accessibility`：实际验证刷新与前进／后退恢复同一会话、断网保存及409冲突时保留草稿、作品加载失败与重试、键盘单选和焦点。已有手机旅程也检查新统计链及图示。人工设置的网络失败属于明确的测试情境，不掩盖其他脚本或服务器错误。

V4新增 `learning_needs_support`、`profile_backup_recovery`、`scope_and_manipulatives`、`changed_content_preview`：验证旧困难暂缓/重开与支持确认、跨浏览器恢复和两个独立学习库之间的完整导入、四种实际数学操作及逐目标入口，并逐页打开由本轮影响分析产生的真实内容地址。页面检查包括文字、公式、图示、可访问控件名称及手机宽度；结果报告记录实际打开页面，保留代表截图。功能测试使用作者答案，不能作真实学生效果证据。

## 文件与验证

默认输出目录为 `build/release/<提交前12位>/`：

| 文件 | 内容 |
| --- | --- |
| `source.zip` | 当前提交中列入发布清单的源码与资料；逐文件核对包内原始字节 |
| `current-branch.bundle` | 当前分支及可达历史，仅含该分支引用；实际恢复到临时仓库、核对HEAD并通过完整对象检查 |
| `evidence.zip` | 当前提交对应的核心／浏览器JSON报告、绑定日志和成功截图 |
| `delivery.json` | 提交、分支、清单哈希、各包大小和SHA-256、门禁数量 |

`release-manifest.json`使用原始字节SHA-256，包括代码、CSS、SVG、Schema、catalog、依赖锁、许可证及字体，不对二进制做换行替换。`data/manifest.json`保留内容构建兼容口径，两者用途不同。

打包检查报告与当前HEAD、发布清单一致，逐项验证日志和PNG哈希。只运行部分浏览器旅程、失败报告、旧提交报告、被替换的日志或缺失截图都会阻止打包。学习数据库、失败trace、旧失败截图和未绑定输出不进入证据包。

Bundle扫描完整可达历史路径，包括合并提交引入的文件。发现被排除的学习库或外部资产会报告路径并停止，不自动改写历史。`git bundle verify`之外还执行实际克隆与 `git fsck --full`，确认包中对象足以恢复当前提交。Git子进程结束且恢复成功后清理本次临时锁，最终目录只允许上述四个交付文件；已有输出目录不覆盖。

## 使用交付包

源码ZIP可解压后直接按README构建和运行，也可执行 `python tools/build_project.py --check` 等不依赖Git历史的检查。没有`.git`时，发布清单按相同发行规则独立扫描文件，仍能发现新增、缺失或篡改；报告不会借用父目录仓库的提交。

`python tools/release_check.py` 及其 `--with-browser` 模式包含固定版本语义审查，需要真实Git仓库以及 `docs/reviews/current.json` 指定的基线提交历史。源码ZIP不包含这些历史；要重跑完整固定版本验收，应先从下述bundle克隆。制作正式发行包还要求完整可达历史、干净提交和绑定该提交的通过报告。

保留本轮完整分支历史时，可以克隆bundle：

```bash
git clone -b codex/cnkg-quality-v4 current-branch.bundle cn-k12-kg-v4
cd cn-k12-kg-v4
```

已有仓库可先导入为一个新本地分支，再检查差异：

```bash
git fetch /absolute/path/current-branch.bundle refs/heads/codex/cnkg-quality-v4:refs/heads/imported-quality-v4
git log --oneline imported-quality-v4
```

这些操作不覆盖现有分支。是否推送远程仍以远程实际引用为准；本地包、提交或验收通过不等于GitHub已经更新。

## 证据界限

所有门禁针对已登记的内容、协议和软件路径。独立教师审核与真实学生效果验证按用户要求暂缓。文件完整、算式正确、程序通过和学生真正学会是不同类型的结论。
