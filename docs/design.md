# Design

## The problem

Every agent scans its own directory for skills. Without a shared source, the same
skill exists N times and drifts. Copy-based sync tools make it worse: they need a
scheduler, they conflict, and a failed run leaves half the copies stale.

`skillsctl` takes the opposite approach: one repository, and each tool's skills
directory is a link into it.

## Single source, per-tool variants

A skill is a directory with a `SKILL.md`. Two names are meaningful:

- `skills/<name>` — the default flavor.
- `skills/<name>__<tool>` — a variant used only by `<tool>`.

Variants exist because tool requirements legitimately differ. Merging them too
early hides a real difference; `drift` and `diff` exist to make the difference
visible before `unify` collapses it.

`manifest.json` is the mapping:

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

## Links, not copies

A link is created with `mklink /J` on Windows (a junction, no admin rights
needed, works for directories on the same volume) and `os.symlink` elsewhere.
The content lives once. `link` backs up any real directory it replaces, so
`rollback` can restore the original layout.

Why not copies: copies need a sync trigger, and every tool that is closed during
the sync stays stale. A link has no sync step.

Why not a package manager: skills are plain files that the tools already read;
there is nothing to install.

## Content identity

A flavor hash covers only what git distributes. Runtime data and caches are
excluded:

- backups: `.bak*`, `*.backup*`, `_backup*`, `_archive*`, `scripts_bak_*`
- runtime: `history/`, `reviews/`
- caches: `.git`, `node_modules`, `__pycache__`, `.pytest_cache`, `.venv`, `.codegraph`
- OS junk: `.DS_Store`, `Thumbs.db`

If runtime data participated, a clean clone would always mismatch. The hash is
computed over the `git ls-files` set when the repo is a git checkout, and over
the whole directory in `selftest` (which runs outside git).

## Gates

`verify` checks six layers and returns non-zero on any error:

1. link exists and points inside the repo;
2. hash matches the manifest;
3. each flavor has `SKILL.md`;
4. frontmatter `name` is valid and equals the skill name;
5. referenced files exist or are registered as external;
6. `SKILL.md` line endings are LF.

`selftest` builds a throwaway repo in the system temp directory and exercises
import, link, verify, drift detection, unify, prune, rollback, exclude, add,
normalize-eol and check-secrets. It never touches the real directories, which is
what makes it safe to run in CI and after every change.

## Failure modes the design accepts

- A junction cannot cross volumes or target a UNC path. On such a tool, use copy
  mode instead.
- A tool may cache its skill list at startup. Changing frontmatter then requires
  a restart; the repo cannot force it.
- A marketplace may delete and re-extract a managed directory. Such directories
  are recorded with `exclude` and never linked.
