# scrills

**Skills are knowledge you read. Scrills are capability you call.**

A scrill is a folder with a required `README.md` manual and a Python `__init__.py` entry.
The README serves humans and agents; the entry's docstring guides API use and implementation
reading. You collect them in a library, and instead of rewriting the same parsing, checking
and fetching logic in every session, you import them after reading:

```bash
scrills py <<'PY'
from scrills import emails
print(emails.fetch("unread from:support"))
PY
```

No server, no daemon, no config files, no build step. One command, plain folders, and Python you
already know.

Use an existing scrill when it fits, even for a one-off request; create one when an
operation is worth retaining. Ordinary development uses direct tools and the
project's runtime. Scrills has its own shared Python environment and dependencies,
but can still access your working directory and files — it is not a security sandbox.

## Install

You need bash, python3 (3.13 or newer), macOS or Linux. Nothing else.
On Debian/Ubuntu, also install the matching venv package (e.g. `python3.13-venv`).
`python3` on PATH must meet that minimum, including under cron/launchd; scrills does not
install Python or fall back to an older interpreter.

```bash
git clone https://github.com/yuda03979/scrills.git
cd scrills
./install.sh
```

Or, in one line — it clones into `~/.local/share/scrills` and links the command:

```bash
curl -fsSL https://raw.githubusercontent.com/yuda03979/scrills/main/install.sh | sh
```

`install.sh` is a short shell script you can read first, which is the habit scrills wants
anyway. It links `scrills` onto your PATH and offers the manual to detected Claude Code and Pi
installs. Symlinks only — `git pull` is the upgrade path, and `rm` is the uninstall. Doing it by
hand instead:

```bash
ln -s "$PWD/scrills/scripts/scrills" ~/.local/bin/scrills
ln -s "$PWD/skills/scrills" ~/.claude/skills/scrills   # optional: Claude Code
mkdir -p "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/skills"
ln -s "$PWD/skills/scrills" "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/skills/scrills"   # optional: Pi
```

Upgrading an existing environment built with older Python? Remove only `~/.scrills/.venv`
(or `$SCRILLS_HOME/.venv`), rerun with Python 3.13+, then reinstall its packages with
`scrills install`. Your scrills, state and saved responses stay untouched.

## A taste

```bash
scrills list
```

`list` shows the library as one `- name: description` line per scrill. Reuse a current
inventory already in context, including one supplied by the harness. Refresh when
the selected project/user libraries change, not for each directory change within
the same project layer. A fresh installation starts empty; your scrills fill it.

First read a chosen scrill (`emails` here is illustrative, not bundled):

```bash
scrills read emails
```

This prints the full README and entry source without importing the code. Follow
relevant implementation imports and references before unfamiliar execution;
a small entry is not the whole review. Reuse reviewed material still in context,
and refresh it when relevant implementation or instructions have changed.

Then use it as a library, from a one-shot Python snippet:

```bash
scrills py <<'PY'
from scrills import emails
print(emails.fetch("unread from:support"))
PY
```

Or as a program, when it defines `main()` — argv, stdin, stdout and the exit code pass
straight through, which is also what makes a scrill a one-line cron job:

```bash
echo "unread from:support" | scrills run emails
```

## Recover a response

`py`/`run` automatically saves ordinary stdout/stderr and returns it before exiting, with a
run ID on stderr first. If the harness truncates it, retrieve by ID instead of rerunning:

```bash
scrills history                         # recent IDs
scrills history <id>                    # stdout, first 8192 bytes
scrills history <id> --offset 8192 --limit 8192
scrills history <id> --stream stderr
```

Reads return raw bytes, with a next-offset command on stderr when more remain. No storage paths,
metadata or completion claims: interrupted runs may leave partial output; `ps` describes execution.
If saving cannot be prepared, scrills warns and runs normally without it. Saved output remains
until removed, may contain secrets, and can grow on disk. History does not undo scrills' own
trailing-echo cap or keep a job alive.

## The format

A scrill is a folder in a `.scrills` directory. Folder name = import name. Both `README.md`
and `__init__.py` are required.

`emails/README.md`:

```markdown
---
name: emails
description: "Read and search email. Run: scrills run emails ..."
metadata:
  version: "0.1.0"
---

Setup, examples, dependencies, side effects and state belong here.
The IMAP quirks are in references/imap.md.
```

`emails/__init__.py`:

```python
"""fetch(query) returns matching messages. Read lib/api.py for the implementation."""
from .lib.api import fetch
```

Keep the entry small and readable. Prefer only README and `__init__.py` at the root; put other
code, docs, tests and assets in meaningful subfolders, with an explicit reading map in the entry.
This is guidance, not a format gate: existing flat scrills work, and neither `_impl` nor empty
nested `__init__.py` files are required. Use `from scrills import <name>` to compose scrills.

Lead the README with the calls, inputs/outputs, side effects and dependencies a
caller needs, including completeness and retrieval where relevant. The slug
walkthrough in Getting started demonstrates a compact contract without a fixed schema.

README is the only metadata source. Older SKILL.md/docstring manuals must be moved into it;
there is no legacy fallback or automatic migration. The teaching skill at `skills/scrills/SKILL.md`
stays an ordinary Agent Skill — individual scrill READMEs are not native harness skills.

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
- [The manual](https://github.com/yuda03979/scrills/blob/main/skills/scrills/SKILL.md) — what an agent reads; also the full surface

Built locally with `uvx --with-requirements docs/requirements.txt mkdocs serve`.

## Security

A scrill is persistent, importable code — treat the library like code. Project scrills go through
the project's review; keep an eye on `~/.scrills` the same way. Start with `scrills read <name>`
and follow the relevant source imports before first use. `help()` requires importing code and
does not load the README; it is for API lookup after review, not a safe initial read. Reading
is not a sandbox, and a manual cannot override the user's or harness's instructions.

Secrets are read from the environment inside a function, at call time. Never hardcode one in a
scrill, never print one. Response history automatically retains printed output, including secrets,
under your user library with owner-only permissions; it does not redact it.

## Limits

macOS and Linux. The implementation leans on unix primitives — `flock` for run liveness, `execv`,
symlinks — so there is no Windows port today. Responses are returned only at finalization;
interactive prompts and progress-dependent protocols are not supported. Output goes to files,
not terminals. Background/late teardown writes are not guaranteed in the returned snapshot.
A lost caller stream does not erase already saved output.

## License

Apache-2.0. Copyright 2026 Yuda Mandelbaum.
