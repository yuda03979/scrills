# examples

A live project layer: `cd` anywhere under this folder and `scrills list` finds `.scrills/` here.

- `harness_config` — point a harness at scrills, from a scrill: for Claude Code, `apply` links `~/.claude/skills/scrills` to this install and adds `Bash(scrills:*)` to the permission allowlist, so sessions discover the manual and run scrills unprompted; `apply --hook` also injects the library listing into every session's start (opt-in), so discovery isn't a per-task race; `status` reports the wiring, `undo` removes exactly what apply added; settings.json is merged, never replaced. Shows: harness wiring as an example scrill instead of core machinery, `SCRILLS_CLI` as how a run finds its own install, `EXITS` naming a status exit.
