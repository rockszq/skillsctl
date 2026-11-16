---
name: demo-skill
description: Minimal example skill used to verify that a tool loads linked skills. Replace this content with a real skill.
---

# Demo skill

This directory exists so a fresh checkout has something to link. It is not
deployed by default; `manifest.json` does not list it.

Try it:

```bash
python skillctl.py add demo-skill --from examples/demo-skill
python skillctl.py link --only demo-skill
python skillctl.py verify
```

Delete it once you have real skills.
