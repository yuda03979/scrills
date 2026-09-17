# getting started with scrills

**Skills are knowledge you read. Scrills are capability you call.**

A scrill is a Python module whose docstring is its manual — the documentation and the implementation are one artifact. You (or your coding agent) collect them in a library, and instead of rewriting the same parsing/checking/fetching logic in every session, you import it:

```bash
scrills py <<'PY'
from scrills import media
print(media.text("paper.pdf", pages="1-3"))
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

`install.sh` makes two symlinks and stops: `scrills` on your PATH and, if Claude Code is installed, the manual at `~/.claude/skills/scrills`. Read it before you run it — it's short, and reading before running is the habit this whole thing is built around. By hand it's the same two lines:

```bash
ln -s "$PWD/scrills/scripts/scrills" ~/.local/bin/scrills
ln -s "$PWD/scrills" ~/.claude/skills/scrills   # optional, see "Teach your agent"
```

(`~/.local/bin` should be on your PATH; any directory on it works — `SCRILLS_BIN=/elsewhere ./install.sh`. Symlink, don't copy the file out: the command reads its version and manual from the `scrills/` folder next to it, and the symlink makes `git pull` your upgrade path.)

That's the whole install. The first `py` or `run` creates a shared environment at `~/.scrills/.venv` — a few seconds, once.

## Kick the tires

The repo ships a live example library:

```bash
cd examples
scrills list
```

`list` is the front door — every scrill, its manual's frontmatter, and which ones run standalone. Try one both ways a scrill can be used.

As a library, from a one-shot Python snippet:

```bash
scrills py <<'PY'
from scrills import subagent
help(subagent)
PY
```

As a program — on a Mac this one gets your attention once a minute until you stop it: a notification while you're at the computer, spoken aloud when you've stepped away.

```bash
scrills run nudge "scrills works" 1
scrills run nudge stop
```

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

- **The docstring is the manual.** Frontmatter (`name`, `description`, `version`) exactly like a SKILL.md, then prose. The description says *when* to use it; the docstrings say *how*. `scrills list` shows the frontmatter without executing anything.
- Optional siblings (`helper.py`, imported as `from .helper import x`) and an optional `references/` folder for longer material — docs read on demand, plus deeper modules importable as `from .references import x`.
- One scrill imports another with `from scrills import <name>`.
- A folder name starting with `_` is an invisible draft.

## The two layers

- **project**: the nearest `.scrills/` walking up from your cwd — committed and reviewed with the project it belongs to.
- **user**: `~/.scrills` — capability that travels with you across projects.

Both are visible everywhere under a project; project wins on a name collision. `scrills where` shows both paths, the python, and how to install a package.

## See what ran

Every `py` and `run` is recorded — metadata only (verb, name, pid, cwd, timing, exit; never code, arguments, or output):

```bash
scrills ps
```
```
running:
  subagent  pid 4242  12m  ~/projects/acme  (cron:nightly)
last 24h: 61 runs - 60 ok, 1 error, 0 died
log: ~/.scrills/.runs/log.jsonl
```

Failures say why: an uncaught exception records its type and where it broke (never its message), and a scrill can name the exit codes its `main()` chooses — `EXITS = {1: "no answer"}` beside it — so a meaningful non-zero exit is recorded as a named outcome, not as an `error`.

A run that never finished — killed, crashed, power loss — surfaces as `died` the first time anything looks. That makes unattended scrills honest: schedule a one-shot scrill from cron or launchd, and `ps` tells you whether it's actually running. A cron line like

```
echo "read app.log, summarize new errors into errors.md" | scrills run subagent --tools "Read,Glob,Write" --allowed-tools "Read,Glob,Write"
```

is the whole "wake an agent on schedule" pattern — the task rides stdin so it never sits in the process list, and the subagent can only do what those flags explicitly grant (by default nothing is approved).

Attribution is one optional convention: export `SCRILLS_WHO=<something>:<something>` (a cron line, a harness) and runs carry the label.

## Third-party packages

All scrills share one environment. A missing package is an ordinary `ModuleNotFoundError`; install it yourself:

```bash
scrills install httpx
```

pip behind it — arguments pass through (`-U`, `==` pins, `-r`), and `scrills where` shows the raw command it stands for. A scrill may declare what it needs in a PEP 723 `# /// script` comment block above its docstring — declarative only. Nothing is ever installed on your behalf.

## Teach your agent

Scrills are built for coding agents: the library is how capability survives the end of a session. Point your harness at the manual, `scrills/SKILL.md`. `install.sh` already did the Claude Code half if `~/.claude/skills/` existed; otherwise, from the clone root (`cd ..` first if you're still in `examples/`):

- Claude Code: `ln -s "$PWD/scrills" ~/.claude/skills/scrills`
- Anything else: paste `scrills/SKILL.md` into the session, or reference its path.

A taught agent checks `scrills list` before writing logic, uses what exists, and saves what proves useful — so the second session starts where the first one ended.

## The fine print

- **IDEs**: the dot in `.scrills` keeps default toolchains out (Pyright project analysis, mypy, pytest). One exception: ruff checks it — add `extend-exclude = [".scrills"]` and `force-exclude = true` to the project's ruff config.
- **Secrets**: read them from the environment inside a function, at call time. Never hardcode one in a scrill, never print one — heredoc code passes through your terminal history and your agent's transcript.
- **Review**: a scrill is persistent, importable code. Treat the library like code — project scrills go through the project's review; keep an eye on `~/.scrills` the same way. The `audit` example adds a second pair of eyes: `scrills run audit <name> && scrills run <name>` runs a scrill only after a sandboxed Claude review of its folder came back clean (cached until the code changes). The review doesn't follow imports — the verdict lists the scrills it uses with their own verdicts, and `scrills run audit --all` covers the whole library.
- **Uninstall**: `rm ~/.local/bin/scrills`, `rm ~/.claude/skills/scrills` if you linked it, delete `~/.scrills` for the library, and delete the clone (`~/.local/share/scrills`, if the one-liner made it). That's everything.
