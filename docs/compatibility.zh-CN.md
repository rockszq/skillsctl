# 工具兼容性

[English](compatibility.md) · **简体中文**

四个工具都是 Node/Electron 应用，用 `readdir(withFileTypes)` 扫描 skill。Windows 上 junction 会被报告为链接而不是目录，所以扫描器必须回落到 `stat`，条目才会被识别。

## 兼容矩阵

| 工具 | skills 目录 | 链接生效 | 是否需重启 | 备注 |
|---|---|---|---|---|
| WorkBuddy | `~/.workbuddy/skills` | 是 | 改 frontmatter 后需要 | skill 列表是启动快照（`.skill-list-cache.json`） |
| Qoder | `~/.qoder/skills` | 是 | 建链后一次 | 以 34 个链接 skill 实测 |
| OpenCode | `~/.config/opencode/skills` | 预期 | 建链后一次 | 同一套扫描器；未单独实测 |
| DSH | `~/.dsh/skills` | 是 | 建链后一次 | `name` 必须是 kebab-case |

"实测"指在 Windows 上重启该工具后，被链接的 skill 能正常加载并调用。版本会变，工具大版本升级后建议复测。

## WorkBuddy

扫描器接受链接：它回落到 `stat`，再校验解析后的路径仍在仓库内部。skill 列表在启动时缓存，所以改过 `description`（决定模型能否召回）之后必须重启。

`~/.workbuddy/skills/` 下由 marketplace 托管的目录会在升级时被重新解压。用 `exclude` 登记它们，不要建链。

## Qoder

重启后链接即注册生效，模型驱动调用与 `/slash` 调用都正常。

## OpenCode

扫描器同属 Node 家族。把链接支持视为"预期"而非"已证"：跑 `verify`、重启 OpenCode，确认 skill 出现。

## DSH

比其余三个多两条硬规则：

1. frontmatter 的 `name` 必须匹配 `^[a-z0-9]+(-[a-z0-9]+)*$`。带下划线的名字会被静默跳过——`link` 在部署时直接拒绝，`verify` 对已部署的条目给出警告。
2. 同名条目按名字去重，优先级低的目录胜出。`__tool` 变体不会作为第二个条目加载。

DSH 还会以更低优先级扫描 `~/.agents/skills`。部署到 `~/.dsh/skills` 可以避免与无关的 `.agents` 工具共享该目录。

## 当某个工具不跟随链接

1. 跑 `python skillctl.py rollback` 还原原始目录。
2. 只对那个工具改用复制模式：源留在仓库，复制到工具目录，再用 `verify` 检测漂移。
3. 提 issue，附上工具名、版本与扫描器行为。
