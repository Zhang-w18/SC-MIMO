# Git 简易使用教程

本文档记录本项目的本地 Git 使用方法。当前项目已经完成初始化，并已有第一个提交：

```text
cc55c9d Initial SC-MIMO project snapshot
```

Git 的作用可以理解为：给项目保存一系列“可回看的快照”。每次完成一小段稳定工作后，就用 `commit` 保存一个版本。

## 1. 当前项目已经完成的初始化

本项目已经执行过：

```bash
cd /Users/zhangwei/Downloads/lls_platform_sc_mimo
git init
git add .
git commit -m "Initial SC-MIMO project snapshot"
```

同时已经新增 `.gitignore`，用于忽略缓存文件和临时输出，例如：

```text
__pycache__/
*.pyc
.DS_Store
outputs/
results/
*.log
```

因此后续你不需要再执行 `git init`。日常只需要查看改动、选择文件、提交版本。

## 2. 最常用的日常流程

每次开始或结束一段工作时，先查看状态：

```bash
cd /Users/zhangwei/Downloads/lls_platform_sc_mimo
git status
```

查看具体改了什么：

```bash
git diff
```

确认要保存后，把文件加入暂存区：

```bash
git add docs/SC_MIMO_understanding_and_simulation.md
```

如果要加入多个文件，可以写多个路径：

```bash
git add docs/SC_MIMO_understanding_and_simulation.md docs/SC_MIMO_implementation_plan.md
```

然后创建一个版本快照：

```bash
git commit -m "Update SC-MIMO understanding notes"
```

完整日常流程就是：

```bash
git status
git diff
git add <要保存的文件>
git commit -m "说明这次改动"
```

## 3. `git status` 怎么看

运行：

```bash
git status --short
```

常见输出含义：

```text
 M docs/file.md
```

表示这个文件已经被修改，但还没有加入下一次 commit。

```text
?? docs/new_file.md
```

表示这是新文件，Git 还没有跟踪。

```text
A  docs/new_file.md
```

表示新文件已经 `git add`，等待 commit。

```text
M  docs/file.md
```

表示修改已经 `git add`，等待 commit。

如果看到：

```text
nothing to commit, working tree clean
```

说明当前所有改动都已经提交，工作区是干净的。

## 4. 查看历史版本

查看最近的提交：

```bash
git log --oneline
```

示例：

```text
cc55c9d Initial SC-MIMO project snapshot
```

查看某一次提交改了什么：

```bash
git show cc55c9d
```

如果只想看文件列表和统计：

```bash
git show --stat cc55c9d
```

## 5. 提交信息怎么写

提交信息最好简短说明“这次为什么改”。例如：

```bash
git commit -m "Document SC-MIMO mapping assumptions"
git commit -m "Add rank2 SC-MIMO mapping tests"
git commit -m "Implement exhaustive MIMO detector"
```

建议一个 commit 只做一类事情。比如“修改文档”和“改 detector 代码”最好分成两个 commit，这样以后回看更清楚。

## 6. 撤销还没提交的改动

如果只是想看看某个文件相对上次 commit 改了什么：

```bash
git diff docs/SC_MIMO_understanding_and_simulation.md
```

如果确认某个文件的未提交修改不要了，可以恢复到上次 commit 的状态：

```bash
git restore docs/SC_MIMO_understanding_and_simulation.md
```

如果文件已经 `git add` 了，但还没有 commit，想把它从暂存区拿出来：

```bash
git restore --staged docs/SC_MIMO_understanding_and_simulation.md
```

注意：`git restore <file>` 会丢弃该文件未提交的修改。执行前最好先用 `git diff` 看清楚。

## 7. 不建议随便运行的命令

下面这些命令可能丢失工作区改动，不熟悉时不要直接运行：

```bash
git reset --hard
git clean -fd
git checkout -- .
```

如果确实需要回退版本，建议先确认当前状态：

```bash
git status
git diff
git log --oneline
```

然后再决定如何操作。

## 8. 本项目建议的提交节奏

建议在这些节点提交：

- 拆分或更新一份重要文档后。
- 实现一个小功能后，例如 rank4 layer-group mapping。
- 新增或修复一组测试后。
- 跑通一个阶段性仿真入口后。
- 大改前先提交当前稳定状态。

不建议提交：

- `__pycache__/`、`.pyc` 等缓存文件。
- 临时输出结果。
- 很大的仿真结果目录。
- 还没想清楚、跑不通、只是临时试验的混乱改动。

## 9. 和我协作时怎么用

你可以让我在改动前后帮你检查：

```text
帮我看一下 git status
帮我看一下这次改了什么
帮我把这次文档修改 commit
帮我把这次代码和测试分成两个 commit
```

我一般会先看：

```bash
git status --short
git diff
```

确认改动范围后，再根据你的要求执行 `git add` 和 `git commit`。
