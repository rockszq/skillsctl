# skillsctl

[English](README.md) · **简体中文**

> 作者：rockszq · 仓库：https://github.com/rockszq/skillsctl · 许可：Apache-2.0

让你机器上所有 AI 编码 Agent 共用同一份 skill 源。

WorkBuddy、Qoder、OpenCode、DSH 这类工具各自扫描自己的 skills 目录。靠复制同步必然漂移：一份修好了，其余几份还留着旧 bug。`skillsctl` 换一种做法——只保留一个仓库，把每个工具的 skills 目录做成指向它的链接，改一处即对全部生效。

```
~/.skills-src/
  skills/<flavor>/        # 唯一内容源，flavor 默认等于 skill 名
  manifest.json           # 每个工具用哪个 flavor + 内容哈希
  tools.json              # 工具名 -> skills 目录
  skillctl.py             # 管理脚本
  _state/                 # 本地审计输出，不入库
~/.skills-backup/<ts>/<tool>/<name>/   # link 之前的原始目录，可还原
```

## 为什么要 flavor

同名 skill 在不同工具里确实可能有正当差异（一个变体校验 WorkBuddy 的 frontmatter，另一个变体的脚本布局不同）。强行合并会静默让其中一个退化，所以允许一个 skill 保存多个版本：

- `skills/<name>` —— 默认版本
- `skills/<name>__<tool>` —— 仅 `<tool>` 使用的版本

由 `manifest.json` 决定每个工具用哪个 flavor。等差异看清了，再用 `unify` 收敛。

## 安装

```bash
git clone https://github.com/rockszq/skillsctl.git ~/.skills-src
cd ~/.skills-src
python skillctl.py init      # 缺失时生成 tools.json 与 manifest.json
python skillctl.py link      # 为每个工具的每个 skill 建链接
python skillctl.py verify    # 期望 0 error / 0 warning
```

`tools.json` 把工具名映射到它的 skills 目录：

```json
{
  "workbuddy": "~/.workbuddy/skills",
  "qoder": "~/.qoder/skills",
  "opencode": "~/.config/opencode/skills",
  "dsh": "~/.dsh/skills"
}
```

增减工具直接改这个文件；`init` 会按上面的默认值生成。仓库位置默认 `~/.skills-src`，可用环境变量 `SKILLSCTL_REPO=/path/to/repo` 覆盖。

## 日常命令

| 命令 | 作用 |
|---|---|
| `init` | 缺失时创建 `skills/`、`manifest.json`、`tools.json` |
| `doctor` | 环境诊断：目录、链接、哈希、过期缓存 |
| `adopt --tool T --path P` | 接入新 Agent 并写入 `tools.json` |
| `status` | 每个 skill 的 flavor 分布 |
| `verify` | 校验链接、哈希、frontmatter、引用文件、行尾 |
| `refresh` | 内容改动后重算 flavor 哈希 |
| `add NAME --from DIR` | 把 skill 纳入仓库并建立链接 |
| `add NAME --empty` | 生成骨架 `SKILL.md` 供填写 |
| `link [--only NAME]` | 备份原目录并替换为链接 |
| `import` | 把各工具现有 skill 复制进仓库（原目录不动） |
| `exclude NAME --tool T --reason R` | 登记一个已知但不受管的 skill |
| `diff NAME` | 显示两个 flavor 的实际差异 |
| `drift [--min-ratio R]` | 列出正文已分叉的多版本 flavor |
| `unify NAME --to F` | 让所有工具统一指向某个 flavor |
| `prune [--apply]` | 清理没有工具指向的孤儿 flavor |
| `rollback [TS]` | 还原 `link` 产生的备份 |
| `check-secrets` | 扫描待提交文件中的硬编码凭证 |
| `normalize-eol` | 把 `SKILL.md` 行尾统一为 LF |
| `selftest` | 在临时目录跑完整自测 |

**仓库内任何改动之后**，都要执行：

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

`verify` 抓的是「文件在，但工具静默跳过它」和「文档引用了不存在的文件」；`selftest` 抓的是「管理器自己坏了」。两者都是门禁（退出码非零即失败），不是提示。

## verify 的六层校验

1. 每个工具目录条目都是链接，且指向仓库内部；
2. 内容哈希与 `manifest.json` 一致；
3. 每个 flavor 都有 `SKILL.md`；
4. frontmatter 的 `name` 合法且等于 skill 名；
5. `SKILL.md` 引用的文件存在（或已登记为外部文件）；
6. `SKILL.md` 使用 LF 行尾。

## 工具兼容性

四个工具都是 Node/Electron 扫描器。Windows 上 junction 会被报告为链接而不是目录，所以扫描器必须回落到 `stat` 才能识别。实测行为：

| 工具 | 链接是否生效 | 备注 |
|---|---|---|
| WorkBuddy | 生效 | 改过 frontmatter 后必须重启：skill 列表是启动时的快照 |
| Qoder | 生效 | 建链后重启一次 |
| OpenCode | 预期生效（未单独实测） | 同一套 Node 扫描器 |
| DSH | 生效 | `name` 必须是 kebab-case，否则静默跳过 |

扫描器的具体行为与细节见 [`docs/compatibility.zh-CN.md`](docs/compatibility.zh-CN.md)。

## 已知限制

- junction 不能跨卷，也不能指向 UNC 路径。跑在 WSL 里的工具请改用复制模式。
- git 不跟踪 junction，仓库里只存在真实目录。若工具升级时删掉了它的 skills 目录，`verify` 会报出来，`link` 可重建。
- marketplace 托管的目录（例如 `~/.workbuddy/skills/superpowers/`）绝不能建链：marketplace 升级时会删除并重新解压。

## 延伸阅读

- [通用工作流](docs/workflow.zh-CN.md)：安装、盘点、迁移、日常维护、接入新 Agent、回滚。
- [Agent 定义](agents/skill-manager.md)：Agent 管理 skill 时的角色、流程与边界（英文，供 Agent 直接引用）。
- [设计](docs/design.zh-CN.md)、[兼容性](docs/compatibility.zh-CN.md)、[迁移](docs/migration.zh-CN.md)。

## 许可

Apache License 2.0。完整条文见 `LICENSE`，署名与再分发要求见 `NOTICE`。再分发时须保留这两个文件，并说明你做了哪些修改——这是 Apache-2.0 第 4 条的要求。
