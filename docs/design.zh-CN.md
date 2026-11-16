# 设计

[English](design.md) · **简体中文**

## 要解决的问题

每个 Agent 各扫自己的目录。没有共享源时，同一个 skill 会存在 N 份并逐渐漂移。基于复制的同步工具更糟：需要调度器、会互相冲突、一次失败就留下半新半旧的副本。

`skillsctl` 取相反的做法：一个仓库，每个工具的 skills 目录都是指向它的链接。

## 单源 + 按工具分版本

一个 skill 就是一个含 `SKILL.md` 的目录。两种命名有意义：

- `skills/<name>` —— 默认 flavor。
- `skills/<name>__<tool>` —— 仅供 `<tool>` 使用的变体。

变体存在是因为工具要求确实会不同。过早合并会掩盖真实差异；`drift` 与 `diff` 的作用就是在 `unify` 收敛之前把差异摆出来。

`manifest.json` 记录映射关系：

```json
{
  "schema": 1,
  "skills": {
    "example": {
      "flavors": { "example": { "files": 3, "hash": "…" } },
      "targets": { "workbuddy": "example", "qoder": "example" }
    }
  }
}
```

## 用链接，不用副本

链接在 Windows 上用 `mklink /J` 创建（junction，无需管理员权限，同卷目录可用），其他平台用 `os.symlink`。内容只存一份。`link` 在替换真实目录之前先备份，因此 `rollback` 能还原原始布局。

为什么不用副本：副本需要同步触发点，同步期间被关闭的工具会一直停留在旧内容。链接没有同步步骤。

为什么不做包管理器：skill 就是普通文件，工具本来就会读它们，没有需要"安装"的东西。

## 内容标识

flavor 哈希只覆盖 git 会分发的内容，运行期数据与缓存被排除：

- 备份：`.bak*`、`*.backup*`、`_backup*`、`_archive*`、`scripts_bak_*`
- 运行期：`history/`、`reviews/`
- 缓存：`.git`、`node_modules`、`__pycache__`、`.pytest_cache`、`.venv`、`.codegraph`
- 系统垃圾：`.DS_Store`、`Thumbs.db`

如果运行期数据参与计算，一个干净的 clone 永远会对不上。仓库是 git 检出时，哈希按 `git ls-files` 的集合计算；在 `selftest`（不在 git 内运行）里则按整个目录计算。

## 门禁

`verify` 校验六层，任一错误即返回非零：

1. 链接存在且指向仓库内部；
2. 哈希与 manifest 一致；
3. 每个 flavor 都有 `SKILL.md`；
4. frontmatter 的 `name` 合法且等于 skill 名；
5. 被引用的文件存在，或已登记为外部文件；
6. `SKILL.md` 行尾为 LF。

`selftest` 在系统临时目录里建一个一次性仓库，跑遍 import、link、verify、drift 检测、unify、prune、rollback、exclude、add、normalize-eol 与 check-secrets。它从不触碰真实目录——这正是它可以放进 CI、也可以在每次改动后安全运行的原因。

## 设计上接受的失效模式

- junction 不能跨卷，也不能指向 UNC 路径。这类工具改用复制模式。
- 工具可能在启动时缓存 skill 列表。改过 frontmatter 后必须重启，仓库无法强制它。
- marketplace 可能删除并重新解压它托管的目录。这类目录用 `exclude` 登记，永不建链。
