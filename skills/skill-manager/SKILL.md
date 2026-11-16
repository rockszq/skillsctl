---
name: skill-manager
description: Use when adding, editing, migrating, unifying or verifying agent skills across WorkBuddy, Qoder, OpenCode, DSH or other tools managed by skillsctl; also use when a skill is missing from one tool, or the same skill drifted between tools.
---

# Skill manager

Maintain one source of truth for agent skills. The repository is `~/.skills-src`
(or `SKILLSCTL_REPO`), each tool's skills directory is a link into it, and
`skillctl.py` is the only supported way to change that layout.

## Before changing anything

1. Read `tools.json` to see which tools are managed.
2. Run `python skillctl.py status` to see the flavor distribution.
3. If a skill is reported missing or stale in one tool, run `verify` before
   editing: the cause is usually a broken link, not missing content.

## Commands

```bash
python skillctl.py status                 # flavors per skill
python skillctl.py verify                 # six-layer gate; non-zero means broken
python skillctl.py refresh                # recompute hashes after any edit
python skillctl.py add NAME --empty       # new skill skeleton
python skillctl.py add NAME --from DIR    # bring an existing directory in
python skillctl.py link [--only NAME]     # replace tool dirs with links
python skillctl.py diff NAME              # compare two flavors
python skillctl.py drift                  # multi-version flavors that diverged
python skillctl.py unify NAME --to F      # collapse to one flavor
python skillctl.py prune [--apply]        # remove orphan flavors
python skillctl.py rollback [TS]          # restore pre-link directories
python skillctl.py exclude NAME --tool T --reason R
python skillctl.py check-secrets          # scan for hard-coded credentials
python skillctl.py selftest               # full suite in a temp directory
```

## Non-negotiable sequence after an edit

```bash
python skillctl.py refresh
python skillctl.py verify && python skillctl.py selftest
```

Editing content without `refresh` makes `verify` report `hash-mismatch` on a
correct change. Skipping `selftest` can hide a broken manager.

## Rules

- Never copy a skill into a tool directory by hand. Add it to the repo and run
  `link`; a hand copy becomes unmanaged drift.
- Never link a marketplace-managed directory (for example
  `~/.workbuddy/skills/superpowers/`). The marketplace deletes and re-extracts
  it. Record it with `exclude`.
- Do not edit `manifest.json` hashes or `generated_at` by hand; `refresh` owns
  them.
- A tool-specific variant is named `<name>__<tool>`. Create one only when the
  difference is intentional and documented.
- For DSH, the frontmatter `name` must be kebab-case. Rename before deploying.
- After changing a skill's `description` or `name`, restart the affected tool:
  WorkBuddy caches its skill list at startup.

## Troubleshooting

| Symptom | Check |
|---|---|
| skill missing in one tool | `verify`; if the link is gone, `link --only NAME` |
| `hash-mismatch` after an edit | run `refresh`, then `verify` again |
| two tools show different content | `status`, then `diff NAME` |
| a skill exists but the model never uses it | frontmatter `description`, then restart the tool |
| link creation fails | junction across volumes or a UNC path; use copy mode for that tool |
| a marketplace skill was overwritten | it was linked by mistake; `exclude` it and restore from the marketplace |
