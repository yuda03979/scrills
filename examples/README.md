# examples

A live project layer: `cd` anywhere under this folder and `scrills list` finds `.scrills/` here.

- `harness_config` — point a harness at scrills, from a scrill. For Claude Code, `apply` links `~/.claude/skills/scrills` to this install and adds `Bash(scrills:*)` to the permission allowlist; `apply --hook` also injects the library listing into every session's start (opt-in), and settings.json is merged, never replaced. For Pi, `apply pi` links the same skill under `$PI_CODING_AGENT_DIR/skills` (default `~/.pi/agent/skills`); Pi already provides bash, so it changes no settings and installs no extension. `status [claude|pi]` reports the wiring; `undo [claude|pi]` removes exactly what apply added. Shows: harness wiring as an example scrill instead of core machinery, `SCRILLS_CLI` as how a run finds its own install, and `EXITS` naming a status exit.
