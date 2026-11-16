# 把现有 skill 迁进仓库

[English](migration.md) · **简体中文**

## 1. 先看现状

```bash
python skillctl.py import
```

`import` 把配置中各工具目录里找到的每个 skill 复制进 `skills/<flavor>/`：第一个副本用 `<name>`，另一个工具内容不同时用 `<name>__<tool>`。原目录不做修改，所以这一步可以反复执行。

## 2. 复核变体

```bash
python skillctl.py status
python skillctl.py diff <name>
python skillctl.py drift
```

`status` 显示每个工具用哪个 flavor。`diff` 打印实际内容差异。`drift` 列出正文相似度低于 85% 的多版本 flavor——通常意味着这些副本是无意间分叉的。

## 3. 建链

```bash
python skillctl.py link --dry-run
python skillctl.py link
```

`link` 把每个真实目录备份到 `~/.skills-backup/<ts>/<tool>/<name>/`，再替换为链接。`--only NAME` 可把操作限制在单个 skill。

## 4. 校验

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

内容改过就必须先 `refresh`：`verify` 是拿文件与 manifest 里的哈希比对，哈希过期会把一次正常编辑误报成 `hash-mismatch`。

## 5. 收敛变体（可选）

只在读过 diff 之后执行：

```bash
python skillctl.py unify <name> --to <flavor>
python skillctl.py link --only <name>
python skillctl.py prune            # 列出孤儿 flavor
python skillctl.py prune --apply    # 删除它们
```

`prune` 拒绝删除没有 git 历史的 flavor，因此未提交的内容不会因为这一步丢失。

## 6. 回滚

```bash
python skillctl.py rollback              # 最近一次备份
python skillctl.py rollback 20260101-120000
```

`rollback` 只移除本仓库创建的链接，并把原始目录移回原位。不受管的目录不会被触碰。

## 新增一个 skill

```bash
python skillctl.py add my-skill --empty
# 编辑 skills/my-skill/SKILL.md
python skillctl.py refresh
python skillctl.py link --only my-skill
python skillctl.py verify
```

`add --from DIR` 改为导入一个已有目录。`add` 拒绝 frontmatter 中 `name` 与 skill 名不一致的情况，并在校验失败时回滚新建的目录。
