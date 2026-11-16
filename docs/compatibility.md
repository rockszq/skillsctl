# Tool compatibility

All four tools are Node/Electron applications and scan skills with
`readdir(withFileTypes)`. On Windows a junction is reported as a link, not a
directory, so the scanner must fall back to `stat` for the entry to register.

## Matrix

| Tool | Skills directory | Link follows | Restart needed | Notes |
|---|---|---|---|---|
| WorkBuddy | `~/.workbuddy/skills` | yes | yes, after frontmatter changes | the skill list is a startup snapshot (`.skill-list-cache.json`) |
| Qoder | `~/.qoder/skills` | yes | once after linking | verified with 34 linked skills |
| OpenCode | `~/.config/opencode/skills` | expected | once after linking | same scanner family; not separately verified |
| DSH | `~/.dsh/skills` | yes | once after linking | `name` must be kebab-case |

"Verified" means the tool was restarted and the linked skill was loaded and
invoked successfully on Windows. Version numbers change; re-verify after a major
tool upgrade.

## WorkBuddy

The scanner accepts links: it falls back to `stat` and then checks that the
resolved path stays inside the repository. The skill list is cached at startup,
so a changed `description` (which drives model recall) needs a restart.

Marketplace directories under `~/.workbuddy/skills/` are managed by the
marketplace and are re-extracted on update. Record them with `exclude`, never
link them.

## Qoder

Links register after a restart; both model-driven and `/slash` invocation work.

## OpenCode

The scanner is the same Node family. Treat link support as expected, not proven:
run `verify`, restart OpenCode, and confirm the skill appears.

## DSH

Two stricter rules than the others:

1. frontmatter `name` must match `^[a-z0-9]+(-[a-z0-9]+)*$`. A name with an
   underscore is skipped silently — `link` refuses it at deploy time and
   `verify` warns for an already-deployed entry.
2. Same-name entries are de-duplicated by name; the lower-rank directory wins.
   `__tool` flavors are not loaded as a second entry.

DSH also scans `~/.agents/skills` at a lower priority. Deploying to
`~/.dsh/skills` avoids sharing that directory with unrelated `.agents` tools.

## When a tool does not follow links

1. Run `python skillctl.py rollback` to restore the original directories.
2. For that tool only, switch to copy mode: keep the source in the repo and copy
   into the tool directory, then run `verify` to detect drift.
3. Open an issue with the tool name, version and scanner behavior.
