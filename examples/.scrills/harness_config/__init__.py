"""
---
name: harness_config
description: Use to point Claude Code or Pi at scrills. Checks and writes only the harness-side wiring needed to discover the manual; Claude Code can also receive an opt-in ambient library listing. Run: scrills run harness_config [status|apply [--hook]|undo] [claude|pi]; nothing changes unless apply is called.
version: 0.1.3
---

status(harness="claude") -> the harness, whether its config root was found, the skill-link
  state, and the paths involved. skill_link: "ok" | "missing" | "elsewhere" (a symlink into
  another tree - apply relinks) | "not a symlink" (a real file or folder - never touched).
  Claude Code also reports its permission rule, hook, and settings path.
apply(harness="claude", hook=False) -> the changes made, [] when already configured. Idempotent.
undo(harness="claude") -> the removals - only what apply added, and a link only while it points
  at this install.

What apply does for Claude Code:
- links ~/.claude/skills/scrills -> this install's scrills/ folder (found through SCRILLS_CLI,
  which every run carries; `which scrills` when imported outside a run).
- adds "Bash(scrills:*)" to permissions.allow in ~/.claude/settings.json.
- with hook=True (--hook): appends one marked SessionStart hook that runs `scrills list` by
  absolute path and injects the listing into every session's context.
settings.json is merged, never replaced; unrelated settings and hooks are preserved.

What apply does for Pi:
- links $PI_CODING_AGENT_DIR/skills/scrills -> this install's scrills/ folder, using
  ~/.pi/agent when PI_CODING_AGENT_DIR is unset.
Pi already provides bash, so there is no permission rule. No settings, extension, env var, or
model configuration is added; --hook is Claude Code only.

As a program: scrills run harness_config [status|apply [--hook]|undo] [claude|pi]
(default: status and claude). Exit 0 configured or done, 1 not configured, 2 usage.
"""
import sys

from .harness import _claude, _pi

HARNESSES = {"claude": _claude, "pi": _pi}

EXITS = {1: "not configured", 2: "usage"}


def status(harness="claude"):
    return HARNESSES[harness].status()


def apply(harness="claude", hook=False):
    return HARNESSES[harness].apply(hook=hook)


def undo(harness="claude"):
    return HARNESSES[harness].undo()


def main():
    args = sys.argv[1:]
    hook = "--hook" in args
    args = [arg for arg in args if arg != "--hook"]
    verb = args[0] if args else "status"
    harness = args[1] if len(args) > 1 else "claude"
    if (
        verb not in ("status", "apply", "undo")
        or len(args) > 2
        or harness not in HARNESSES
        or (hook and (verb != "apply" or harness != "claude"))
    ):
        print("usage: scrills run harness_config [status|apply [--hook]|undo] [claude|pi]", file=sys.stderr)
        return 2
    module = HARNESSES[harness]
    if verb == "status":
        report = module.status()
        for key, value in report.items():
            print(f"{key}: {value}")
        return 0 if module.configured(report) else 1
    changes = module.apply(hook=hook) if verb == "apply" else module.undo()
    for line in changes:
        print(line)
    if not changes:
        print("nothing to change")
    if verb == "apply":
        return 0 if module.configured(module.status()) else 1
    return 0
