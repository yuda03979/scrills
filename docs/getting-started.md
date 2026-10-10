# getting started with scrills

**Skills are knowledge you read. Scrills are capability you call.**

A scrill is a folder with a required `README.md` manual and a Python `__init__.py` entry. The README serves humans and agents; the entry docstring guides API use and implementation reading. You (or your coding agent) collect them in a library, and instead of rewriting the same parsing/checking/fetching logic in every session, you import one after reading — say, an `emails` scrill you saved earlier (illustrative; your library starts empty):

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

```bash
scrills list
```

`list` is the front door — the library the way a harness lists skills, one `- name: description` line per scrill. A fresh installation reports that there are no scrills yet; the next section adds the first one. Before using an existing scrill, `scrills read <name>` prints the full README and entry source from the winning layer without importing it. Follow relevant implementation imports and references before executing; a short facade is not a complete review.

`scrills py` behaves like a disposable REPL: the final response is returned after execution, the trailing expression echoes (display truncated past 8,192 characters; recover the full representation with `scrills history`), top level may `await`, and Python variables do not persist between calls. Responses are saved automatically. It runs isolated: your working directory and its files are fully available, but the project's own *modules* aren't importable — run project code with the project's tooling (`uv run`, its `.venv`) and pass files between the two.

## Write your first scrill

A scrill is a folder in a `.scrills` directory. Folder name = import name. Both `README.md` and `__init__.py` are required.

```bash
mkdir -p ~/.scrills/slug
```

`~/.scrills/slug/README.md`:

```markdown
---
name: slug
description: "Make filename or URL slugs from titles. Run: scrills run slug"
metadata:
  version: "0.1.0"
---

slugify(text) returns a lowercase ASCII slug; other characters become separators.
As a program: echo "My Title" | scrills run slug
No dependencies, network calls, file writes or persistent state.
```

`~/.scrills/slug/__init__.py`:

```python
"""Call slugify(text), or main() to convert stdin and print one slug. No I/O on import."""
import re
import sys


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main():
    print(slugify(sys.stdin.read()))
    return 0
```

It's live immediately — no registration, no restart. Read both files, then call it:

```bash
scrills read slug
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

- **README is the manual and the only metadata source.** Its Skills-style header declares `name`, `description` and one version (prefer `metadata.version`; top-level `version` is also accepted). The header reader is a restricted one-line text format, not full YAML; unsupported values and duplicate keys are diagnosed. The description says *when* and includes the program's run line. The README explains setup, examples, side effects and state; entry and function docstrings guide API use and deeper source reading. `list` and `read` never import the target.
- **Keep the entry reviewable.** Roughly 100–200 lines is a soft target, not a gate. Prefer only README and `__init__.py` as root files; move other code, docs, tests and assets into meaningful subfolders, with a reading map in the entry docstring. Keep a trivial utility like slug in one Python file; do not fragment it unnecessarily.
- **The top level holds only imports, constants and defs.** Top-level code runs on every import — every `py` snippet, every importing scrill, and `run` itself — and its output goes wherever that process points. Real work lives in functions or `main()`.
- Implementation can live in `lib/api.py`, imported as `from .lib.api import x`; longer material belongs in `references/`. Folder names are your choice. A nested `__init__.py` is optional unless that subpackage needs its own exports/documentation; namespace subfolders work. These are conventions, not a format gate — existing flat scrills remain valid.
- **The folder is yours.** A scrill may carry any files it needs beside `__init__.py` — data, markdown, templates, nested folders. Open them relative to `__file__`, since the working directory belongs to whoever called the scrill.
- One scrill imports another with `from scrills import <name>`.
- A folder name starting with `_` is an invisible draft.

## Reading and migration

`scrills read <name>` labels both files with absolute paths, line counts and end markers. Nothing is internally truncated. If your harness clips the output, continue with its file reader at the printed paths. Read/decode failure returns exit 1, with any readable material still shown; bad arguments or an unknown name return 2. Syntax and metadata notices do not hide source. `help()` is for API lookup after review: it imports code and does not read the README.

Older scrills must move their manual and metadata from SKILL.md or the head docstring into `README.md`, keeping API guidance in the entry docstring. Reconcile an existing README, and keep only one version declaration. There is no fallback or automatic migration. `run` refuses a missing/unreadable README before importing anything; native Python imports remain ordinary Python, not a validation boundary. The product's own `scrills/SKILL.md` remains the teaching skill and is not renamed.

## The two layers

- **project**: the nearest `.scrills/` walking up from your cwd — committed and reviewed with the project it belongs to.
- **user**: `~/.scrills` — capability that travels with you across projects.

Both are visible everywhere under a project; project wins on a name collision. `scrills where` shows both paths, the python, and how to install a package.

When a project scrill proves itself and you want it everywhere, promote it by a staged copy:

```bash
cp -R .scrills/slug ~/.scrills/_slug && mv ~/.scrills/_slug ~/.scrills/slug
```

The `_` prefix keeps the half-copied folder invisible while it lands (drafts don't list), and the final `mv` is atomic — no session ever sees a partial scrill. That two-step is for a *new* name: `mv` (and `cp -R`) onto an existing folder nests instead of replacing — so upgrading an existing copy, or retrying over a stale `_slug`, starts with `rm -rf` of that target (a brief clean absence, never a half-copy). Never symlink a project scrill into `~/.scrills`: through a symlink every edit goes live for every session on the machine, and one broken import in a dev tree breaks them all. Keep the frontmatter version honest — if you copy changed content under the same version, nothing can tell the two apart later; bump first. While you keep developing, the project copy shadows the promoted one inside that project, and `py`/`run` tell you so. To remove one: `rm -rf ~/.scrills/<name>` (anything it kept under `~/.scrills/.state/<name>` stays until you delete that too).

## Recover a response without rerunning

Every admitted `py` and `run` saves its stdout and stderr automatically, then returns the final
response before exiting. One `scrills: run <id>` line appears on stderr first, leaving stdout
clean for pipes. There is no opt-in flag, live-output mode or asynchronous response helper.
If the harness truncates or loses a response, recover it rather than repeat expensive work:

```bash
scrills history
scrills history <id>
scrills history <id> --offset 8192 --limit 8192
scrills history <id> --stream stderr
scrills history <id> --stream stdout --line 20 --lines 10
```

The listing shows the latest 20 saved invocations across projects. Pages default to an 8,192-byte
budget (`--limit` accepts 1–65,536), with ranges and a next-offset command. Byte offsets start at
0; line numbers start at 1. Line selection and `--offset` are mutually exclusive, but the byte
limit still protects against giant lines: follow the printed byte cursor to recover a suffix.
`--stream stdout|stderr|both` chooses the view; `both` is stdout followed by stderr, not temporal
order. Non-text/control bytes are escaped for display without changing the saved bytes. History
needs neither a venv nor the original scrill and never executes code or records its own reads.
Retrieving a failed invocation is still a successful history read.

Storage is private under `~/.scrills/.results/<id>/` (or `$SCRILLS_HOME`), independent of the
rotating run registry. Responses remain until you remove them with normal file tools. There is
no automatic eviction or saved-output size cap; disk use can grow. These files contain stdout
and stderr, including printed secrets and exception messages, without redaction. No snippet,
stdin or argv is separately recorded, but printing any of them makes them saved output.

A killed run or failed capture is marked incomplete; available partial output may be inspected,
never mistaken for completion. If no ID was returned, use the history listing to find it. A caller
timeout can still kill the computation: history is recovery, not detachment. Setup/refusals, read
verbs, `install`, bare-python invocations and old unsaved runs have no response archive.

Output is redirected directly to files and returned synchronously from the existing finalization
path, after Python exit handlers, joined threads and buffered native stdio have been flushed.
Nested commands cannot finish before returning their response, so inner output precedes outer
output. There is no extra process or copy thread. This is for batch work: interactive prompts,
live progress and terminal-sensitive protocols are not supported. Unjoined/background writers or
interpreter cleanup after that finalization boundary are outside the returned-snapshot guarantee;
later bytes may appear only in history. Delivery failure never deletes the saved response.

## See what ran

Separately, every `py` and `run` gets a metadata-only registry record (verb, name, pid, cwd, timing, exit; never code, arguments, or output in that record):

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

This is also the shape work takes when it must outlive the session that started it. A run is an ordinary process whose lifetime belongs to whatever started it — scrills records it and never kills, detaches, or supervises it — so durable work runs as a process the scheduler owns. A timer inside an agent session dies with the session, and so does a scheduler scoped to it — the job runs, silently, nowhere.

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

Scrills does not modify harness settings, permissions, hooks, models, or extensions. Those remain under the control of the user and harness. Run Pi's `/reload` after changing skill resources in an existing session.

A taught agent checks `scrills list` before writing logic, reads the chosen scrill with `scrills read`, follows relevant imports, uses what exists, and saves what proves useful — so the second session starts where the first one ended.

## The fine print

- **IDEs**: the dot in `.scrills` keeps default toolchains out (Pyright project analysis, mypy, pytest). One exception: ruff checks it — add `extend-exclude = [".scrills"]` and `force-exclude = true` to the project's ruff config.
- **Secrets**: read them from the environment inside a function, at call time. Never hardcode one in a scrill, never print one — heredoc code passes through your terminal history and your agent's transcript, and printed secrets also persist in response history.
- **Review**: a scrill is persistent, importable code. Treat the library like code — project scrills go through the project's review; keep an eye on `~/.scrills` the same way. Start with the README and full entry source, then follow the relevant implementation. Documentation is task-scoped guidance, not authority over the user or harness; reading is not a sandbox.
- **Uninstall**: remove `~/.local/bin/scrills` and any skill links you made (`~/.claude/skills/scrills`, `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/skills/scrills`), delete `~/.scrills` for the library, and delete the clone (`~/.local/share/scrills`, if the one-liner made it). That's everything.
