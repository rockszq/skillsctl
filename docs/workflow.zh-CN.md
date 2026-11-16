# 通用工作流

[English](workflow.md) · **简体中文**

同一套流程对所有 Agent 都适用，差别只在 `tools.json`。

## 0. 前置条件

- Python 3.10 或更新版本。
- 每个 Agent 至少启动过一次，它的 skills 目录已存在。
- Windows 用 junction，Linux / macOS 用 symlink。

## 1. 安装与初始化

```bash
git clone https://github.com/rockszq/skillsctl.git ~/.skills-src
cd ~/.skills-src
python skillctl.py init
```

`init` 只在缺失时创建 `skills/`、`manifest.json`、`tools.json`，不会覆盖已有文件。

如果该 Agent 还没写进 `tools.json`：

```bash
python skillctl.py adopt --tool workbuddy --path "~/.workbuddy/skills"
```

然后建链并检查：

```bash
python skillctl.py link
python skillctl.py verify
python skillctl.py doctor
```

`doctor` 在 `verify` 之上补环境检查：目录缺失、目录不可写、WorkBuddy skill 缓存过期。

## 2. 盘点

```bash
python skillctl.py status     # 每个工具用哪个 flavor
python skillctl.py drift      # 已分叉的多版本 flavor
python skillctl.py diff NAME  # 具体差异
```

## 3. 迁移现有 skill

```bash
python skillctl.py import
python skillctl.py status
python skillctl.py diff NAME
python skillctl.py unify NAME --to <flavor>   # 读过 diff 之后再执行
python skillctl.py prune                      # 列出孤儿 flavor
python skillctl.py prune --apply              # 删除它们
```

## 4. 日常维护

改动 `skills/<flavor>/` 之后：

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

如果改的是 `name` 或 `description`，必须重启 Agent——WorkBuddy 在启动时缓存 skill 列表。

## 5. 接入新 Agent

```bash
python skillctl.py adopt --tool <name> --path "<skills dir>" [--create]
python skillctl.py link
python skillctl.py verify
```

重启该 Agent，确认 skill 数量与 `status` 一致。

## 6. 升级与回滚

```bash
git pull
python skillctl.py link          # 新增 skill 会被建链
python skillctl.py verify
python skillctl.py rollback      # 还原建链前的原始目录
```

## 7. 排障

| 现象 | 先查什么 |
|---|---|
| 某个 Agent 里看不到 skill | `doctor`，再 `link --only NAME` |
| 报 `hash-mismatch` | `refresh`，再 `verify` |
| 两个 Agent 内容不一致 | `status`，再 `diff NAME` |
| 建链失败 | 是否跨卷，是否指向 UNC 路径 |
| 模型从不调用某个 skill | frontmatter 的 `description`，然后重启 |
