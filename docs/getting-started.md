# getting started with scrills

**Skills are knowledge you read. Scrills are capability you call.**

A scrill is a Python module that carries its own manual — in its head docstring, or as a SKILL.md file beside the code: one folder holding both. You (or your coding agent) collect them in a library, and instead of rewriting the same parsing/checking/fetching logic in every session, you import it — say, an `emails` scrill you saved earlier (illustrative; your library starts empty):

```bash
scrills py <<'PY'
from scrills import emails
print(emails.fetch("unread from:support"))
PY
```

No server, no daemon, no config files, no build step. One command, plain folders, and Python you already know.

## Install

You need: bash, python3 (3.9 or newer), macOS or Linux. Nothing else. (Debian/Ubuntu: `sudo apt install python3-venv` once — Debian ships python3 without the venv module.)

```bash
git clone https://github.com/yuda03979/scrills.git
cd scrills
./install.sh
```

Or in one line, which clones into `~/.local/share/scrills` and links from there:

```bash
curl -fsSL https://raw.githubusercontent.com/yuda03979/scrills/main/install.sh | sh
```

`install.sh` makes symlinks and stops: `scrills` on your PATH, plus the manual for detected Claude Code and Pi installs. Read it before you run it — it's short, and reading before running is the habit this whole thing is built around. By hand:

```bash
mkdir -p ~/.local/bin && ln -s "$PWD/scrills/scripts/scrills" ~/.local/bin/scrills
mkdir -p ~/.claude/skills && ln -s "$PWD/scrills" ~/.claude/skills/scrills   # optional: Claude Code
mkdir -p "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/skills"
ln -s "$PWD/scrills" "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/skills/scrills"   # optional: Pi
```

(`~/.local/bin` should be on your PATH; any directory on it works — `SCRILLS_BIN=/elsewhere ./install.sh`. Symlink, don't copy the file out: the command reads its version and manual from the `scrills/` folder next to it, and the symlink makes `git pull` your upgrade path.)

That's the whole install. The first `py` or `run` creates a shared environment at `~/.scrills/.venv` — a few seconds, once.

## Kick the tires

The repo ships a live example layer:

```bash
cd examples
scrills list
```

`list` is the front door — the library the way a harness lists skills, one `- name: description` line per scrill. What ships today is `harness_config`, harness wiring for Claude Code and Pi (see "Teach your agent" below); the library becomes interesting as you fill it, and the next section writes your first scrill.

`scrills py` behaves like a disposable REPL: stdout passes through, the trailing expression echoes (truncated past 8,192 characters — print to a file for the full thing), top level may `await`, and nothing persists between calls — anything worth keeping goes in a file. It runs isolated: your working directory and its files are fully available, but the project's own *modules* aren't importable — run project code with the project's tooling (`uv run`, its `.venv`) and pass files between the two.

## Write your first scrill

A scrill is a folder in a `.scrills` directory. Folder name = import name. Entry is always `__init__.py`.

```bash
mkdir -p ~/.scrills/slug
```

`~/.scrills/slug/__init__.py`:

```python
"""
---
name: slug
description: Use when you need a clean filename or url slug from a title.
version: 0.1.0
---

slugify(text) returns the slug. As a program: echo "My Title" | scrills run slug
"""
import re
import sys


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main():
    print(slugify(sys.stdin.read()))
    return 0
```

It's live immediately — no registration, no restart:

```bash
scrills py <<'PY'
from scrills import slug
slug.slugify("Hello, World! — Part 2")
PY
```
```
'hello-world-part-2'
```

And because it defines `main()` (reads `sys.argv`/stdin, return value is the exit code — the console-scripts idiom), it's also a program:

```bash
echo "Hello, World" | scrills run slug
```

The anatomy, in order of importance:

- **The manual is the docstring — or a `SKILL.md` beside `__init__.py`.** SKILL.md-shaped frontmatter (`name`, `description`, `version` — top-level or under `metadata:`, both read), then prose; a skill folder becomes a scrill by adding `__init__.py`. Declare the manual once: when both the file and the docstring open with frontmatter, SKILL.md wins and `list` says so. The description says *when* to use it — it is all `list` shows, so a program scrill's description also carries its run line; the docstrings say *how* — every `.py` file opens with a head docstring saying what it holds. `scrills list` parses files without executing anything.
- **The top level holds only imports, constants and defs.** Top-level code runs on every import — every `py` snippet, every importing scrill, and `run` itself — and its output goes wherever that process points. Real work lives in functions or `main()`.
- Optional siblings (`helper.py`, imported as `from .helper import x`) and an optional `references/` folder for longer material — docs read on demand, plus deeper modules importable as `from .references import x`.
- **The folder is yours.** A scrill may carry any files it needs beside `__init__.py` — data, markdown, templates, nested folders. Open them relative to `__file__`, since the working directory belongs to whoever called the scrill.
- One scrill imports another with `from scrills import <name>`.
- A folder name starting with `_` is an invisible draft.

## The two layers

- **project**: the nearest `.scrills/` walking up from your cwd — committed and reviewed with the project it belongs to.
- **user**: `~/.scrills` — capability that travels with you across projects.

Both are visible everywhere under a project; project wins on a name collision. `scrills where` shows both paths, the python, and how to install a package.

When a project scrill proves itself and you want it everywhere, promote it by a staged copy:

```bash
cp -R .scrills/slug ~/.scrills/_slug && mv ~/.scrills/_slug ~/.scrills/slug
```

The `_` prefix keeps the half-copied folder invisible while it lands (drafts don't list), and the final `mv` is atomic — no session ever sees a partial scrill. That two-step is for a *new* name: `mv` (and `cp -R`) onto an existing folder nests instead of replacing — so upgrading an existing copy, or retrying over a stale `_slug`, starts with `rm -rf` of that target (a brief clean absence, never a half-copy). Never symlink a project scrill into `~/.scrills`: through a symlink every edit goes live for every session on the machine, and one broken import in a dev tree breaks them all. Keep the frontmatter version honest — if you copy changed content under the same version, nothing can tell the two apart later; bump first. While you keep developing, the project copy shadows the promoted one inside that project, and `py`/`run` tell you so. To remove one: `rm -rf ~/.scrills/<name>` (anything it kept under `~/.scrills/.state/<name>` stays until you delete that too).

## See what ran

Every `py` and `run` is recorded — metadata only (verb, name, pid, cwd, timing, exit; never code, arguments, or output):

```bash
scrills ps
```
```
running:
  report  pid 4242  12m  ~/projects/acme  (cron:nightly)
last 24h: 61 runs - 60 ok, 1 error, 0 died
log: ~/.scrills/.runs/log.jsonl
```

Failures say why: an uncaught exception records its type and where it broke (never its message), and a scrill can name the exit codes its `main()` chooses — `EXITS = {1: "no answer"}` beside it — so a meaningful non-zero exit is recorded as a named outcome, not as an `error`.

A run that never finished — killed, crashed, power loss — surfaces as `died` the first time anything looks. That makes unattended scrills honest: schedule a one-shot scrill from cron or launchd — `echo app.log | scrills run report` — and `ps` tells you whether it actually ran, and how it ended.

One macOS constraint for scheduled work: launchd and cron jobs can't read TCC-protected folders (`~/Desktop`, `~/Documents`, `~/Downloads`), even through symlinks — the same command that works in your terminal dies with EPERM. Keep the scrills clone outside those folders; the one-line installer's default (`~/.local/share/scrills`) already is. And a scheduled job's working directory decides its project layer: cron starts you in `$HOME`, launchd in `/` — a scheduled *project* scrill needs the job to `cd` into the project first (or launchd's `WorkingDirectory`), or it resolves the user layer only and `run` fails with *no scrill named …* in the job's log.

Attribution is one optional convention: export `SCRILLS_WHO=<something>:<something>` (a cron line, a harness) and runs carry the label.

## Third-party packages

All scrills share one environment. A missing package is an ordinary `ModuleNotFoundError`; install it yourself:

```bash
scrills install httpx
```

pip behind it — arguments pass through (`-U`, `==` pins, `-r`), and `scrills where` shows the raw command it stands for. A scrill may declare what it needs in a PEP 723 `# /// script` comment block above its docstring — declarative only. Nothing is ever installed on your behalf.

## Teach your agent

Scrills are built for coding agents: the library is how capability survives the end of a session. Point your harness at the manual, `scrills/SKILL.md`. `install.sh` already did this for detected Claude Code and Pi installs; otherwise the skill-link lines are in the by-hand Install block above, and for any other harness, paste `scrills/SKILL.md` into the session or reference its path.

The `harness_config` example automates the wiring from inside scrills itself:

```bash
cd examples
scrills run harness_config apply       # Claude Code (the default)
scrills run harness_config apply pi    # Pi
```

For Claude Code, apply links the skill and adds the Bash permission rule that lets sessions run `scrills` unprompted. Add `--hook` to the Claude Code command and it also injects the library listing into every session's start (opt-in — a SessionStart hook running `scrills list`), so the agent knows what exists before any task arrives. For Pi, apply only links the skill under `$PI_CODING_AGENT_DIR/skills` (default `~/.pi/agent/skills`): Pi already provides bash, and no settings or extension are needed. Run Pi's `/reload` after changing resources in an existing session. The example lives in the project layer, so copy it into `~/.scrills` to have it anywhere; `status` shows what's wired, and `undo` removes exactly what apply added.

A taught agent checks `scrills list` before writing logic, uses what exists, and saves what proves useful — so the second session starts where the first one ended.

## The fine print

- **IDEs**: the dot in `.scrills` keeps default toolchains out (Pyright project analysis, mypy, pytest). One exception: ruff checks it — add `extend-exclude = [".scrills"]` and `force-exclude = true` to the project's ruff config.
- **Secrets**: read them from the environment inside a function, at call time. Never hardcode one in a scrill, never print one — heredoc code passes through your terminal history and your agent's transcript.
- **Review**: a scrill is persistent, importable code. Treat the library like code — project scrills go through the project's review; keep an eye on `~/.scrills` the same way. Read a scrill's `__init__.py` before first use: it's about a screen of Python, and that read is the review.
- **Uninstall**: remove `~/.local/bin/scrills` and any skill links you made (`~/.claude/skills/scrills`, `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/skills/scrills`), delete `~/.scrills` for the library, and delete the clone (`~/.local/share/scrills`, if the one-liner made it). That's everything.
