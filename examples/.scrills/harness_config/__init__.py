"""
---
name: harness_config
description: Use to point a coding harness at scrills. Claude Code today - checks and writes the harness-side wiring (the skill symlink, one Bash permission rule) so sessions discover scrills and run it unprompted. Nothing changes unless apply is called.
version: 0.1.0
---

status(harness="claude") -> {harness, found, skill_link, permission, link, settings, target}
  skill_link: "ok" | "missing" | "elsewhere" (a symlink into another tree - apply relinks) |
  "not a symlink" (a real file or folder - reported, never touched). permission: "present" |
  "absent". found: the harness's config directory exists.
apply(harness="claude") -> the changes made, [] when already configured. Idempotent.
undo(harness="claude") -> the removals - only what apply adds: the rule, and the link only
  while it points at this install.

What apply does for claude, and all it does:
- links ~/.claude/skills/scrills -> this install's scrills/ folder (found through SCRILLS_CLI,
  which every run carries; `which scrills` when imported outside a run), so sessions pick the
  manual up as a skill.
- adds "Bash(scrills:*)" to permissions.allow in ~/.claude/settings.json, so sessions run
  scrills commands without asking. Merged, never replaced: every other key passes through
  untouched, rewritten as standard two-space JSON. A settings.json that does not parse is
  refused loudly and nothing is touched.
Nothing else: no hooks, no env vars, no model settings - the harness stays the user's.

As a program: scrills run harness_config [status|apply|undo]   (default: status)
Exit 0 configured or done, 1 not configured, 2 usage.
"""
import sys

from . import _claude

HARNESSES = {"claude": _claude}

EXITS = {1: "not configured", 2: "usage"}


def status(harness="claude"):
    return HARNESSES[harness].status()


def apply(harness="claude"):
    return HARNESSES[harness].apply()


def undo(harness="claude"):
    return HARNESSES[harness].undo()


def main():
    args = sys.argv[1:]
    verb = args[0] if args else "status"
    harness = args[1] if len(args) > 1 else "claude"
    if verb not in ("status", "apply", "undo") or len(args) > 2 or harness not in HARNESSES:
        print("usage: scrills run harness_config [status|apply|undo] [claude]", file=sys.stderr)
        return 2
    module = HARNESSES[harness]
    if verb == "status":
        report = module.status()
        for key in ("harness", "found", "skill_link", "permission", "link", "settings", "target"):
            print(f"{key}: {report[key]}")
        return 0 if module.configured(report) else 1
    changes = module.apply() if verb == "apply" else module.undo()
    for line in changes:
        print(line)
    if not changes:
        print("nothing to change")
    if verb == "apply":
        return 0 if module.configured(module.status()) else 1
    return 0
