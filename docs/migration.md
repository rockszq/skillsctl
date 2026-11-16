# Migrating existing skills into the repo

**English** · [简体中文](migration.zh-CN.md)

## 1. Inspect

```bash
python skillctl.py import
```

`import` copies every skill it finds in the configured tool directories into
`skills/<flavor>/`, where `<flavor>` is `<name>` for the first copy and
`<name>__<tool>` when another tool has different content. Originals are not
modified, so this step is repeatable.

## 2. Review variants

```bash
python skillctl.py status
python skillctl.py diff <name>
python skillctl.py drift
```

`status` shows which tools use which flavor. `diff` prints the actual content
difference. `drift` lists multi-version flavors whose bodies are less than 85%
similar, which usually means the copies diverged unintentionally.

## 3. Link

```bash
python skillctl.py link --dry-run
python skillctl.py link
```

`link` backs up each real directory to `~/.skills-backup/<ts>/<tool>/<name>/`
and replaces it with a link. `--only NAME` limits the operation to one skill.

## 4. Verify

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

`refresh` must run after any content change: `verify` compares against hashes in
the manifest, and stale hashes turn a normal edit into a false `hash-mismatch`.

## 5. Collapse variants (optional)

Only after reading the diff:

```bash
python skillctl.py unify <name> --to <flavor>
python skillctl.py link --only <name>
python skillctl.py prune            # list orphan flavors
python skillctl.py prune --apply    # delete them
```

`prune` refuses to delete a flavor with no git history, so uncommitted content
cannot be lost this way.

## 6. Roll back

```bash
python skillctl.py rollback              # latest backup
python skillctl.py rollback 20260101-120000
```

`rollback` removes only the links this repo created and moves the original
directories back. Unmanaged directories are untouched.

## Adding a new skill

```bash
python skillctl.py add my-skill --empty
# edit skills/my-skill/SKILL.md
python skillctl.py refresh
python skillctl.py link --only my-skill
python skillctl.py verify
```

`add --from DIR` imports an existing directory instead. `add` refuses a
frontmatter `name` that does not equal the skill name, and rolls the new
directory back when validation fails.
