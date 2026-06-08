# Project skills

Drop project-scoped Claude Code skills here. Each skill is its own folder with a `SKILL.md`:

```
.claude/skills/
  <skill-name>/
    SKILL.md            # required: frontmatter + instructions
    references/         # optional: extra files the skill can read on demand
    scripts/            # optional: helper scripts the skill can run
```

`SKILL.md` starts with YAML frontmatter, then markdown instructions:

```markdown
---
name: <skill-name>            # kebab-case, matches the folder name
description: Use when ...      # one line; this is what Claude matches against to decide relevance
---

## What this skill does

Instructions Claude follows when the skill is invoked. Be specific about steps,
inputs, and outputs. Link extra files with relative paths (references/foo.md).
```

Notes:
- The `description` is the trigger. Write it as "Use when <situation>" so it's matched reliably.
- Keep `SKILL.md` focused; move long material into `references/` and point to it.
- Invoke a skill via the Skill tool or by typing `/<skill-name>`.
- Project skills here are available in this repo only; user-wide skills live in `~/.claude/skills/`.
