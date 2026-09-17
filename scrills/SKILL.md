---
name: scrills
description: Skills you import instead of read. A library of Python capability, layered per project and per user, driven from bash. Use it before writing logic a scrill may already hold, for computation beyond a one-liner, and for finished work with clear inputs.
compatibility: Needs bash and python3 (3.9 or newer) on macOS or Linux (Debian/Ubuntu also need the python3-venv package). Nothing to install.
license: Apache-2.0
metadata:
  version: "0.2.7"
---

# scrills

Skills are knowledge you read; scrills are capability you call. A scrill is a Python module whose docstring is its SKILL.md — the manual and the implementation are one artifact. The command is `scrills`.

Check the library before writing logic: `scrills list` shows every scrill, its manual's frontmatter, and which ones run standalone.

## The library

Two layers, merged, project wins on a name collision:

- **project**: the nearest `.scrills/` directory walking up from where you are — reviewed and committed with the project. One it doesn't take: a `.scrills` owned by another user is ignored, and `list`, `py`, `run` and `where` say so.
- **user**: `~/.scrills` (or `$SCRILLS_HOME`) — capability that travels across projects.

A collision is process-wide: the project scrill also replaces the user one inside every other scrill's imports, so `py` and `run` print one stderr line naming what's shadowed. `scrills where` shows both paths, the python, the resolver, and how to install a package.

## Using a scrill

Run Python once with the whole library importable:

```bash
scrills py <<'PY'
from scrills import emails
help(emails)
PY
```

The description says when to use a scrill; `help()` says how. But `help()` renders manuals, not code — before first using a scrill you didn't write, read its `__init__.py` (about a screen of Python). That read is the review: what it imports, what it touches, what running it will do.

`scrills py` is one-shot: stdout passes through, the trailing expression echoes like a REPL, top level may `await`, and **nothing persists between calls** — keep anything worth keeping in files. A huge trailing expression echoes truncated (8,192 characters; `SCRILLS_ECHO_CAP` overrides, 0 = uncapped) — print to a file when you want it all.

Runs are isolated: the working directory stays yours (read and write project files freely), but project *modules* aren't importable and `PYTHONPATH` is ignored. Project code runs with the project's own tooling — `uv run`, its `.venv` — in its own command; pass files between the two, not imports.

A scrill that defines `main()` is also a program: `scrills run <name> [args...]`. Arguments, stdin, stdout and the exit code pass straight through. Use it for finished work with clear inputs. Being a program, it also slots straight into cron or launchd — one-shot checks, scheduled work.

Input reaches a scrill four ways: function arguments when imported — the main way; argv and stdin when run as a program (`echo data | scrills run it a b`); environment variables inherited from the caller (secrets travel this way, read at call time); and files, relative to your working directory. The catch: `scrills py`'s stdin already carries the code, so there is none left for data. Pass big data as a file path, never pasted into the snippet; `some-command | scrills py` feeds that output to the compiler — write it to a file first, run the command from inside the Python, or use `scrills run`, which does take stdin.

## Runs are recorded

Every `py` and `run` leaves a metadata record — verb, name, a unique run id, pid, cwd, timing, exit, who, and which scrills it imported with their versions — never code, arguments, or output. Failures say why: an uncaught exception records its type and where it broke (`error_type`, `error_at` — for a syntax error, where the compiler found it), never its message. A run scrill can name the exits its `main()` chooses — `EXITS = {1: "no answer"}` beside it — and such a run is recorded as an `outcome` carrying that name instead of an `error`. When `TRACEPARENT` is in the environment the run joins that trace and sets its own id for everything it starts, so cron → subagent → a nested `scrills py` reads as one chain; unset, each run starts its own. `scrills ps` is the one place to look: what's running now (held exact by a kernel lock each run keeps for its life), what died (a run that never finished — killed, crashed, power loss — surfaces the first time anything looks), and a summary of the last 24 hours. History is plain JSONL at `~/.scrills/.runs/log.jsonl`, size-capped, inspectable like any file; recording failures never break a run.

Attribution is one convention: export `SCRILLS_WHO=<harness>:<session>` (a harness extension, a cron line) and runs carry it; unset, they record `tty` or `detached`.

## Writing a scrill

When logic proves useful beyond the moment, save it as a scrill: a folder in the right layer, entry always `__init__.py`.

```
.scrills/
  emails/
    __init__.py      the scrill: manual in the docstring, code below it
    helper.py        optional siblings
    references/      optional extra material: docs read on demand, deeper modules
    templates.json   anything else it needs - only __init__.py is required
```

The module docstring opens with frontmatter exactly like a SKILL.md:

```python
"""
---
name: emails
description: Use when you need to read or send email for this project.
version: 0.1.0
---

fetch(query) returns matching messages. send(to, subject, body) sends one.
Details per function in its docstring; the IMAP quirks are in references/imap.md.
"""
```

- The folder name is the import name: a valid Python identifier, lowercase — so `_` where a skill name would have `-`, the one deliberate difference from the skills format. Keep frontmatter `name` the same — `list` flags a mismatch, and a missing description or version.
- The description says *when* to use it; the rest of the docstring and each function's docstring say *how*. Longer material goes in `references/*.md`, named in the docstring.
- **The top level holds only imports, constants and defs.** Top-level code runs on every import — every `py` snippet, every importing scrill, and `run` itself (it imports before calling `main()`) — and its output goes wherever that process's stdout points: scrills never captures it, the run log never records it. Real work lives in functions or `main()`.
- **The folder is the scrill's own.** Beyond `__init__.py` it may hold whatever it needs — data, markdown, fixtures, nested folders. Open them relative to `__file__`; the working directory belongs to whoever called you, not to the scrill.
- State that survives between runs goes under `~/.scrills/.state/<name>/` (`$SCRILLS_HOME`-aware) — the scrill's own name, its own folder, never another's.
- **A sync function never calls `asyncio.run()`.** A `py` snippet that awaits anywhere runs inside an event loop, where `asyncio.run()` raises. For concurrency inside sync code use threads; offer an `async def` twin for callers that await.
- A name starting with `_` is a draft, invisible until renamed.
- To make it runnable, define `main()`: it reads `sys.argv`, its return value is the exit code — the console-scripts idiom. No `__main__.py`. Name the non-zero exits it chooses in a module constant beside it — `EXITS = {1: "no answer", 2: "usage"}` — and those runs are recorded as an `outcome` with that name, not an `error`.

Imports inside a scrill, nothing special:

```python
from .helper import parse        # a sibling in this folder
from .references import ocr      # a module under references/ (top-level rule applies there too)
from scrills import media        # another scrill
import httpx                     # third-party, from the shared environment
```

A project scrill may import a user scrill; the reverse works only inside that project — treat it as a smell.

Third-party needs may be declared, PEP 723 style, above the docstring:

```python
# /// script
# dependencies = ["httpx"]
# ///
```

Declarative only — readers and `uv` tooling understand it; scrills installs nothing from it.

## IDE and linters

The dot in `.scrills` keeps default toolchains out: Pyright/Pylance excludes `**/.*` from project analysis, mypy's crawler skips it, pytest doesn't collect from it. Two known exceptions: **ruff does check it** — add `extend-exclude = [".scrills"]` plus `force-exclude = true` (the latter keeps explicit-path runs out too) to the project's ruff config; and editors still analyze a scrill file *while you have it open*, where a missing-package squiggle means the package is absent from the project's venv, not necessarily from the scrills one.

## Packages

`scrills py` and `scrills run` share one environment (`~/.scrills/.venv`, created on first use). A missing package is an ordinary `ModuleNotFoundError`:

```bash
scrills install httpx
```

pip behind it, arguments pass through (`-U`, `==` pins, `-r`); `scrills where` shows the raw command it stands for. Nothing is installed unless asked.

## Secrets

Read a secret from the environment inside a function, at call time. Don't hardcode it in a scrill, and don't print it — heredoc code passes through the transcript like any other command.
