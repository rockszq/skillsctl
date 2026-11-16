# Workflow

The same sequence works for every agent. Only `tools.json` differs.

## 0. Prerequisites

- Python 3.10 or newer.
- Each agent has been started once, so its skills directory exists.
- Windows uses junctions; Linux and macOS use symlinks.

## 1. Install and initialize

```bash
git clone https://github.com/rockszq/skillsctl.git ~/.skills-src
cd ~/.skills-src
python skillctl.py init
```

`init` creates `skills/`, `manifest.json` and `tools.json` if they are missing.
It never overwrites an existing file.

If the agent is not in `tools.json` yet:

```bash
python skillctl.py adopt --tool workbuddy --path "~/.workbuddy/skills"
```

Then create the links and check them:

```bash
python skillctl.py link
python skillctl.py verify
python skillctl.py doctor
```

`doctor` adds environment checks on top of `verify`: missing directories, a
non-writable directory, and a stale WorkBuddy skill cache.

## 2. Inventory

```bash
python skillctl.py status     # which flavor each tool uses
python skillctl.py drift      # multi-version flavors that diverged
python skillctl.py diff NAME  # the actual difference
```

## 3. Migrate existing skills

```bash
python skillctl.py import
python skillctl.py status
python skillctl.py diff NAME
python skillctl.py unify NAME --to <flavor>   # only after reading the diff
python skillctl.py prune                      # list orphan flavors
python skillctl.py prune --apply              # remove them
```

## 4. Daily maintenance

Edit `skills/<flavor>/`, then:

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

Restart the agent if the `name` or `description` changed; WorkBuddy caches its
skill list at startup.

## 5. Add another agent

```bash
python skillctl.py adopt --tool <name> --path "<skills dir>" [--create]
python skillctl.py link
python skillctl.py verify
```

Restart the agent, then confirm the skill count matches `status`.

## 6. Upgrade or roll back

```bash
git pull
python skillctl.py link          # new skills get linked
python skillctl.py verify
python skillctl.py rollback      # restore the pre-link directories
```

## 7. Troubleshoot

| Symptom | First check |
|---|---|
| skill missing in one agent | `doctor`, then `link --only NAME` |
| `hash-mismatch` | `refresh`, then `verify` |
| two agents show different content | `status`, then `diff NAME` |
| link creation fails | junction across volumes or a UNC path |
| model never uses a skill | frontmatter `description`, then restart |
