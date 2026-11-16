# skillsctl

**English** · [简体中文](README.zh-CN.md)

> Author: rockszq · Source: https://github.com/rockszq/skillsctl · License: Apache-2.0

One source of truth for the skills of every AI coding agent on your machine.

WorkBuddy, Qoder, OpenCode, DSH and similar tools each scan their own skills
directory. Keeping copies in sync drifts: one copy gets a fix, the others keep
the bug. `skillsctl` keeps a single repository and makes every tool's skills
directory a link to it, so one edit reaches all of them.

```
~/.skills-src/
  skills/<flavor>/        # the only content source; flavor defaults to the skill name
  manifest.json           # skill -> flavor per tool + content hash
  tools.json              # tool name -> skills directory
  skillctl.py             # the manager
  _state/                 # local audit output, not committed
~/.skills-backup/<ts>/<tool>/<name>/   # pre-link originals, restorable
```

## Why flavors

The same skill name can legitimately differ per tool (one variant validates
WorkBuddy frontmatter, another ships a different script layout). Forcing a
merge silently regresses one of them, so a skill may keep several versions:

- `skills/<name>` — the default version
- `skills/<name>__<tool>` — a tool-specific version

`manifest.json` decides which flavor each tool uses. Once the differences are
understood, `unify` collapses them.

## Install

```bash
git clone https://github.com/rockszq/skillsctl.git ~/.skills-src
cd ~/.skills-src
python skillctl.py init      # writes tools.json + manifest.json if missing
python skillctl.py link      # creates one link per tool and skill
python skillctl.py verify    # expect 0 error / 0 warning
```

`tools.json` maps a tool name to its skills directory:

```json
{
  "workbuddy": "~/.workbuddy/skills",
  "qoder": "~/.qoder/skills",
  "opencode": "~/.config/opencode/skills",
  "dsh": "~/.dsh/skills"
}
```

Edit it to add or remove tools; `init` copies the defaults above. The repository
location defaults to `~/.skills-src` and can be overridden with
`SKILLSCTL_REPO=/path/to/repo`.

## Daily commands

| Command | What it does |
|---|---|
| `init` | create `skills/`, `manifest.json` and `tools.json` if missing |
| `doctor` | environment diagnosis: directories, links, hashes, stale cache |
| `adopt --tool T --path P` | connect a new agent and write `tools.json` |
| `status` | flavor distribution per skill |
| `verify` | links, hashes, frontmatter, referenced files, line endings |
| `refresh` | recompute flavor hashes after editing content |
| `add NAME --from DIR` | bring a skill into the repo and link it |
| `add NAME --empty` | create a skeleton `SKILL.md` to fill in |
| `link [--only NAME]` | back up originals and replace them with links |
| `import` | copy existing per-tool skills into the repo (originals untouched) |
| `exclude NAME --tool T --reason R` | record a known, unmanaged skill |
| `diff NAME` | show the actual difference between two flavors |
| `drift [--min-ratio R]` | list multi-version flavors whose bodies diverged |
| `unify NAME --to F` | point every tool at one flavor |
| `prune [--apply]` | remove orphan flavors no tool points at |
| `rollback [TS]` | restore a backup made by `link` |
| `check-secrets` | scan committed files for hard-coded credentials |
| `normalize-eol` | convert `SKILL.md` line endings to LF |
| `selftest` | run the whole suite in a temp directory |

**After any edit inside the repo**, run:

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

`verify` catches "the file exists but the tool silently skips it" and "the
document references a file that is not there". `selftest` catches "the manager
itself broke". Both are gates, not hints.

## How verify works

Six layers:

1. every tool entry is a link that points inside the repo;
2. content hash matches `manifest.json`;
3. every flavor has a `SKILL.md`;
4. frontmatter `name` is valid and equals the skill name;
5. files referenced by `SKILL.md` exist (or are registered as external);
6. `SKILL.md` uses LF line endings.

## Tool compatibility

All four tools scan with Node/Electron. A junction is reported as a symlink, not
a directory, so the scanner must fall back to `stat`. Tested behavior:

| Tool | Link support | Notes |
|---|---|---|
| WorkBuddy | junction follows | restart after changing frontmatter: the skill list is a startup snapshot |
| Qoder | junction follows | restart once after linking |
| OpenCode | expected (not separately tested) | same Node scanner family |
| DSH | junction follows | `name` must be kebab-case; a non-kebab name is skipped silently |

Details and the exact scanner behavior are in
[`docs/compatibility.md`](docs/compatibility.md).

## Limits

- Junctions do not cross volumes and do not point at UNC paths. On a tool that
  runs inside WSL, use copy mode for that tool instead.
- Git does not track junctions; only real directories live in the repo. If a
  tool upgrade deletes its skills directory, `verify` reports it and `link`
  recreates it.
- Marketplace-managed directories (e.g. `~/.workbuddy/skills/superpowers/`) must
  never be linked: the marketplace deletes and re-extracts them on update.

## Further reading

- [Workflow](docs/workflow.md): install, inventory, migrate, maintain, add an
  agent, roll back.
- [Agent definition](agents/skill-manager.md): the role, procedure and
  boundaries an agent follows when it manages skills.
- [Design](docs/design.md), [compatibility](docs/compatibility.md),
  [migration](docs/migration.md).
- Chinese versions: [README](README.zh-CN.md) ·
  [workflow](docs/workflow.zh-CN.md) · [design](docs/design.zh-CN.md) ·
  [compatibility](docs/compatibility.zh-CN.md) ·
  [migration](docs/migration.zh-CN.md).

## License

Apache License 2.0. See `LICENSE` for the full text and `NOTICE` for the
attribution. When redistributing, keep both files and state the changes you
made — Apache-2.0 section 4 requires it.

