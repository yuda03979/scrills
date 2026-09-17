# scrills

**Skills are knowledge you read. Scrills are capability you call.**

A scrill is a Python module whose docstring is its manual — the documentation and the
implementation are one artifact. You collect them in a library, and instead of rewriting the same
parsing, checking and fetching logic in every session, you import it:

```bash
scrills py <<'PY'
from scrills import media
print(media.text("paper.pdf", pages="1-3"))
PY
```

No server, no daemon, no config files, no build step. One command, plain folders, and Python you
already know.

## Install

You need bash, python3 (3.9 or newer), macOS or Linux. Nothing else.
(Debian/Ubuntu: `sudo apt install python3-venv` once.)

```bash
git clone https://github.com/yuda03979/scrills.git
cd scrills
./install.sh
```

Or, in one line — it clones into `~/.local/share/scrills` and links the command:

```bash
curl -fsSL https://raw.githubusercontent.com/yuda03979/scrills/main/install.sh | sh
```

`install.sh` is twenty lines of shell you can read first, which is the habit scrills wants
anyway. It links `scrills` onto your PATH and, if Claude Code is installed, links the manual into
`~/.claude/skills/`. Two symlinks, nothing else — `git pull` is the upgrade path, and
`rm` is the uninstall. Doing it by hand instead:

```bash
ln -s "$PWD/scrills/scripts/scrills" ~/.local/bin/scrills
ln -s "$PWD/scrills" ~/.claude/skills/scrills   # optional
```

## A taste

```bash
cd examples && scrills list
```

`list` is the front door: every scrill in your library, the frontmatter of its manual, and which
ones run standalone. The repo ships four — `subagent`, `media`, `nudge` and `audit` — as a live
project layer you can read and run.

Then use one. As a library, from a one-shot Python snippet:

```bash
scrills py <<'PY'
from scrills import media
help(media)
PY
```

Or as a program, because it defines `main()` — argv, stdin, stdout and the exit code pass
straight through, which is also what makes a scrill a one-line cron job:

```bash
echo "summarize today's errors into errors.md" | scrills run subagent
```

## The format

A scrill is a folder in a `.scrills` directory. Folder name = import name. Entry is always
`__init__.py`, and its docstring is the manual.

```python
"""
---
name: emails
description: Use when you need to read or send email for this project.
version: 0.1.0
---

fetch(query) returns matching messages. send(to, subject, body) sends one.
The IMAP quirks are in references/imap.md.
"""
import imaplib


def fetch(query):
    ...
```

That's the whole format. Optional siblings, an optional `references/` folder, and
`from scrills import <name>` to use one scrill from another.

## Two layers

- **project** — the nearest `.scrills/` walking up from where you are, committed and reviewed
  with the project it belongs to.
- **user** — `~/.scrills`, capability that travels with you across projects.

Both are visible everywhere under a project; project wins on a name collision.

## Why

An MCP server is opaque and costs a tool call per turn. A skill is prose, and prose is ambiguous.
A scratchpad is rewritten from scratch every session. A scrill is readable source you import: one
run can combine many calls and keep the intermediate results out of the context window, and what
proved useful in one session is still there in the next one.

Reading is cheaper than writing, and a scrill is reusable between sessions, between agents, and
between people.

## Docs

- [Getting started](https://github.com/yuda03979/scrills/blob/main/docs/getting-started.md) — install, first use, first scrill, layers, packages
- [The manual](https://github.com/yuda03979/scrills/blob/main/scrills/SKILL.md) — what an agent reads; also the full surface
- [Examples](https://github.com/yuda03979/scrills/blob/main/examples/README.md) — four working scrills that ship with the repo

Built locally with `uvx --with-requirements docs/requirements.txt mkdocs serve`.

## Security

A scrill is persistent, importable code — treat the library like code. Project scrills go through
the project's review; keep an eye on `~/.scrills` the same way. Read a scrill's `__init__.py`
before first use: it's about a screen of Python, and that read is the review. The `audit` example
adds a second pair of eyes.

Secrets are read from the environment inside a function, at call time. Never hardcode one in a
scrill, never print one.

## Limits

macOS and Linux. The implementation leans on unix primitives — `flock` for run liveness, `execv`,
symlinks — so there is no Windows port today.

## License

Apache-2.0. Copyright 2026 Yuda Mandelbaum.
