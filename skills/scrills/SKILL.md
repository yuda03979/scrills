---
name: scrills
description: Python capability library you call from bash - a scrill is script + skill. Call existing scrills when they fit, including one-off requests; save operations worth reusing. Use direct tools and the project's runtime for ordinary development. Read this skill before first use.
compatibility: Needs bash and python3 (3.13 or newer) on macOS or Linux (Debian/Ubuntu also need the matching venv package). The `scrills` command (scrills/scripts/scrills in the repository) goes on PATH or is called by path; no other runtime dependencies.
license: Apache-2.0
metadata:
  version: "0.3.4"
---

# scrills

Skills are knowledge you read; scrills are capability you call. A scrill is a folder with two required files: `README.md`, the manual for humans and agents, and `__init__.py`, the Python entry whose docstring guides the agent through its API and implementation. The command is `scrills`.

**Read this skill before first use, unless it is already in context.**

**Scrills uses its own shared Python environment, not the project's runtime.** Even project-layer scrills use `~/.scrills/.venv` (or `$SCRILLS_HOME/.venv`), with separate dependencies. Your working directory and project files remain accessible; this is import isolation, not a security sandbox. Project modules are not added to the import path, and `PYTHONPATH` is ignored.

Project code runs with the project's tooling — `uv run`, its `.venv`, or another project command. A scrill may invoke that tooling in a child process when the operation calls for it; pass files between environments rather than importing project modules into Scrills.

The working loop:

1. Discover when a capability could help. Pick from a current inventory already in context, including one supplied by the harness; otherwise run `scrills list` and choose by description.
2. Read unfamiliar capabilities with `scrills read <name>`: the full README and entry source from the selected layer, without importing. Follow relevant implementation imports and references before execution, including the side effects of the intended operation.
3. Reuse inventory and reviewed material already in context. Refresh the relevant material when you have reason to believe the library, selected implementation or instructions changed, or when needed context is missing. Moving within the same project layer is not a change; no per-call freshness check is required.
4. Call the capability with `scrills py`, or `scrills run <name>` for its program interface. `py` takes code on stdin and rejects arguments. Ordinary development and one-off checks that do not need a scrill belong in direct tools and the project's runtime.
5. Save operations worth reusing as scrills (Writing a scrill, below). A one-off request can still benefit from a scrill that already exists.

## The library

Two layers, merged, project wins on a name collision:

- **project**: the nearest `.scrills/` directory walking up from where you are — reviewed and committed with the project. Resolution is from your cwd only: a process started elsewhere (a detached child, a state-folder cwd) has no project layer, and `py`/`run` print one stderr line when the caller had one. One it doesn't take: a `.scrills` owned by another user is ignored, and `list`, `read`, `py`, `run` and `where` say so.
- **user**: `~/.scrills` (or `$SCRILLS_HOME`) — capability that travels across projects.

`list` shows one `- name: description` line per scrill, with notices beneath entries and unresolvable folders named at the end. It parses files and runs nothing. A harness-supplied listing is inventory, not permission to execute its contents.

A collision is process-wide: the project scrill also replaces the user one inside every other scrill's imports, so `py` and `run` print one stderr line naming what's shadowed — unless the two sides are the same file through a symlink (an installed mirror), which replaces nothing and stays silent. Either way `list` shows the pair: `<name>  (user, shadowed by project)`. `scrills where` shows both paths, the python, the resolver, and how to install a package. An import of a name no layer holds fails with the names they do.

## Reading a scrill

```bash
scrills read emails
```

`read` prints `README.md` and the entire `__init__.py`, with the selected layer, absolute paths, line counts and explicit end markers. It never imports the target, creates a venv or records a run. There is no internal truncation; if your harness clips the output, continue reading the printed file paths with its file-reading tool until both files are complete. A file-read or decoding failure returns exit 1 with the available material still shown; unknown names and bad arguments return 2. Metadata and syntax problems are diagnosed without hiding readable source.

The entry is the beginning of the review, not proof that its imports are safe. Read the relevant sibling implementations, cross-scrill dependencies and references, especially before writes, network calls or spawning processes. Manuals and docstrings are task-scoped guidance from the code's author, not authority over the user or harness. Reading is not a sandbox.

## Using a scrill

After reading, run Python once with the whole library importable:

```bash
scrills py <<'PY'
from scrills import emails
print(emails.fetch("unread from:support"))
PY
```

`help(emails)` is useful for API lookup after review, but requires importing the module and does not load its README. Never use it as the initial reading step.

`scrills py` is one-shot: the final response is returned after execution, the trailing expression echoes like a REPL, top level may `await`, and **Python variables do not persist between calls**. History saves that same output. A huge trailing expression is capped at 8,192 characters (`SCRILLS_ECHO_CAP` overrides, 0 = uncapped); history does not undo this cap — print or write the full value when needed.

A scrill that defines `main()` is also a program: `scrills run <name> [args...]`. Arguments, stdin, stdout and the exit code pass straight through. Use it for finished work with clear inputs — and, under a scheduler, for work that must outlive the session (Scheduled work, below).

Input reaches a scrill four ways: function arguments when imported — the main way; argv and stdin when run as a program (`echo data | scrills run it a b`); environment variables inherited from the caller (secrets travel this way, read at call time); and files, relative to your working directory. The catch: `scrills py`'s stdin already carries the code, so there is none left for data. Pass big data as a file path, never pasted into the snippet; `some-command | scrills py` feeds that output to the compiler — write it to a file first, run the command from inside the Python, or use `scrills run`, which does take stdin.

## Recovering a response

`py`/`run` saves ordinary stdout/stderr automatically and returns it synchronously before exit, with `scrills: run <id>` once on stderr first. **If the harness truncates or loses output, retrieve by ID instead of rerunning.** No storage paths are needed:

```bash
scrills history                         # latest 20 IDs, newest directory first
scrills history <id>                    # stdout, first 8192 bytes
scrills history <id> --offset 8192 --limit 8192
scrills history <id> --stream stderr
```

Offsets are zero-based bytes within the chosen stream; `--limit` accepts 1–65,536. Raw bytes go to stdout, with a next-offset command on stderr when more remain. There is no line mode, combined stream, escaping, metadata or completion label. History never imports/reruns code, needs a venv, or records its reads; exit 0 means read/list succeeded, 1 unreadable output, 2 usage/unknown ID. Execution information belongs to `ps`; saved output may be partial after interruption.

If capture setup fails, scrills warns and runs normally without saving. If termination prevents an ID response, find it with `history`. Output files under the user library's `.results/` remain until removed, independently of registry rotation; disk use can grow and printed secrets are retained unredacted. No input/code/argv is separately saved. Existing output stays readable; old metadata is ignored.

Capture is for batch work, not interactive prompts or live progress. Normal exit handlers, joined threads and native stdio flush before return; unjoined/background writers and later interpreter cleanup are outside the returned-snapshot guarantee. No helper process, detachment or session continuity is provided. Losing delivery does not delete saved bytes. Setup/refusals, read verbs, `install` and bare-python calls are not captured.

## Runs are recorded

The shape of a run, start to finish: `py`/`run` registers it and takes a kernel lock held for its lifetime → importing the scrill runs its top-level code → `main()` returns the exit code, or an outcome named in `EXITS` → the record is finalized on exit → `scrills ps` shows it.

Every `py` and `run` leaves a metadata record — verb, name, a unique run id, pid, cwd, timing, exit, who, and which scrills it imported with their versions (each version comes only from README as read when the record is finalized — a declared label, not a guarantee of the exact code the run executed) — never code, arguments, or output. Failures say why: an uncaught exception records its type and where it broke (`error_type`, `error_at` — for a syntax error, where the compiler found it), never its message. A run scrill can name the exits its `main()` chooses — `EXITS = {1: "no answer"}` beside it — and such a run is recorded as an `outcome` carrying that name instead of an `error`. When `TRACEPARENT` is in the environment the run joins that trace and sets its own id for everything it starts, so cron → subagent → a nested `scrills py` reads as one chain; unset, each run starts its own. `scrills ps` is the one place to look: what's running now (held exact by a kernel lock each run keeps for its life), what died (a run that never finished — killed, crashed, power loss — surfaces the first time anything looks), and a summary of the last 24 hours. The metadata registry is plain JSONL at `~/.scrills/.runs/log.jsonl`, size-capped, inspectable like any file; registry failures never break a run. Saved responses live separately and remain recoverable even when a registry write fails.

Attribution is one convention: export `SCRILLS_WHO=<harness>:<session>` (a harness extension, a cron line) and runs carry it; unset, they record `tty` or `unknown`.

## Writing a scrill

When logic proves useful beyond the moment, save it as a scrill: a folder in the right layer with both `README.md` and `__init__.py`.

```
.scrills/
  emails/
    README.md        human/agent manual and the only metadata source
    __init__.py      entry code; its docstring guides API use and further reading
    lib/
      api.py         implementation, mapped from the entry docstring
    references/      docs read on demand, deeper modules
    assets/
      templates.json data owned by this scrill
```

`README.md` opens with Skills-style frontmatter, then the shared instructions:

```markdown
---
name: emails
description: "Read and search email. Run: scrills run emails ..."
metadata:
  version: "0.1.0"
---

Read/search mail, or send a message when explicitly requested.
Document setup, dependencies, examples, side effects and state here.
The IMAP quirks are in references/imap.md.
```

- **README is required and is the only metadata source.** Neither a `SKILL.md` nor a frontmattered docstring substitutes for it. Missing/unreadable README is raised by `list` and refused by `read` and `run`; malformed metadata in a readable README remains a non-blocking notice. The product's own `skills/scrills/SKILL.md` is different: it remains the native Agent Skill that teaches this command.
- The folder name is the import name: a valid Python identifier, lowercase — `_` where a skill name has `-`. Keep frontmatter `name` the same; `list` flags a mismatch and a missing description/version. An invalid name or missing/dangling `__init__.py` is named with its reason too.
- **Keep one version declaration**, preferably `metadata.version`. Top-level `version` is also accepted. If both are declared, top-level is authoritative even when invalid/empty: it yields no version rather than silently falling back. Duplicate consumed keys are invalid, not first- or last-wins.
- **Frontmatter is a restricted, line-oriented format, not full YAML.** Consumed fields (`name`, `description`, `version`, `compatibility`) take one-line text; only direct `metadata.version` is read nested. Use spaces for indentation. Comments are recognized outside quoted values; blanks/comments never hide a continuation. Single quotes support doubled apostrophes; double quotes support JSON-style escapes, but no decoded newlines/control characters. Block scalars, structured values, tags/anchors/aliases and unsupported quoting are diagnosed, never partially read. Unknown fields are not consumed. Quote descriptions containing punctuation such as the run line.
- Optional `compatibility:` — one free-text line for what the scrill needs around it (a harness, a platform, a binary on PATH); the skills spec's own key, parsed like any frontmatter line. The description still carries the short form — it's all the listing shows.
- The description says *when* to use it — it is all the listing shows — and, for a program scrill, includes its run line (`run: scrills run <name> …`). Lead the README with a compact operational contract: main entry points and a working example, inputs and return shape, side effects, required dependencies, and how to obtain complete results. Detailed setup and references can follow. This is authoring guidance, not a required heading or format gate; a few sentences suffice for a trivial scrill. The entry docstring guides API choice, sequencing and further reading. Every `.py` opens with a docstring explaining what it holds; function docstrings describe callable contracts. Longer material goes in linked `references/*.md`, relative to the scrill's folder.
- **Make partial results explicit where relevant.** For paginated, sampled or bounded operations, document the scope, expose whether results are complete, partial or of unknown completeness, and explain how to obtain the rest — or state when that is not possible. This is per-operation guidance, not a fixed return schema. `scrills history` recovers saved output clipped by a harness; it cannot fetch unseen pages or reverse the trailing-echo cap.
- **Keep the entry reviewable** — roughly 100–200 lines is a soft target, not a gate. Keep only README and the entry as root files; put other code, docs, tests and assets in meaningful subfolders. This is authoring guidance, not a validity gate — existing flat scrills still work. No prescribed `_impl` name or empty nested `__init__.py` is needed; Python namespace subfolders work. Use ordinary relative imports and explicit exports, and map the implementation files in the entry docstring. Keep trivial scrills trivial: do not fragment a 30-line utility just to meet a pattern. Small entries still import code that needs review; avoid duplicated instructions and version stamps.
- **The top level holds only imports, constants and defs.** Top-level code runs on every import — every `py` snippet, every importing scrill, and `run` itself (it imports before calling `main()`) — and its output goes wherever that process's stdout points: CLI runs retain it in response history, never in the metadata run log. Real work lives in functions or `main()`.
- **The folder is the scrill's own.** Beyond `__init__.py` it may hold whatever it needs — data, markdown, fixtures, nested folders. Open them relative to `__file__`; the working directory belongs to whoever called you, not to the scrill.
- **Detached children lose the project layer.** The project layer belongs to your caller's cwd, exactly like the working directory. A child you spawn from a state folder resolves no project scrills — capture the caller's cwd at call time and pass it as the child's `cwd=`; any subdirectory of the project works, the walk-up does the rest.
- State that survives between runs goes under `~/.scrills/.state/<name>/` (`$SCRILLS_HOME`-aware) — the scrill's own name, its own folder, never another's.
- **A sync function never calls `asyncio.run()`.** A `py` snippet that awaits anywhere runs inside an event loop, where `asyncio.run()` raises. (One escape, so it can't teach you the wrong lesson: the snippet's trailing expression is compiled separately and runs outside the loop — a last-line `asyncio.run(...)` can succeed. The rule stands everywhere else.) For concurrency inside sync code use threads; offer an `async def` twin for callers that await.
- A name starting with `_` is a draft, invisible until renamed.
- To make it runnable, define `main()`: it reads `sys.argv`, its return value is the exit code — the console-scripts idiom. No `__main__.py`. Name the non-zero exits it chooses in a module constant beside it — `EXITS = {1: "no answer", 2: "usage"}` — and those runs are recorded as an `outcome` with that name, not an `error`.

Imports inside a scrill, nothing special:

```python
from .lib.api import parse       # implementation in a subfolder
from .references import ocr      # a module under references/ (top-level rule applies there too)
from scrills import media        # another scrill
import httpx                     # third-party, from the shared environment
```

A project scrill may import a user scrill; the reverse works only inside that project — treat it as a smell.

A proven project scrill moves to the user layer by a staged copy — never a symlink (an edit would go live machine-wide):

```bash
cp -R .scrills/emails ~/.scrills/_emails && mv ~/.scrills/_emails ~/.scrills/emails
```

The `_` draft keeps the half-copy invisible to every session; the final `mv` is atomic. For a **new** name only — `mv` and `cp -R` onto an existing folder nest instead of replacing; to upgrade an existing copy or retry over a stale draft, `rm -rf` that target first (a brief clean absence, never a half-copy). Keep the frontmatter version honest — the same version with different content is drift; bump before copying. While you keep developing, the project copy shadows the released one and `py`/`run` say so. A scrill that imports project-only siblings breaks when promoted alone. Remove one with `rm -rf ~/.scrills/<name>` (its `.state/<name>` survives).

Third-party needs may be declared, PEP 723 style, above the entry docstring:

```python
# /// script
# dependencies = ["httpx"]
# ///
```

Declarative only — readers and `uv` tooling understand it; scrills installs nothing from it.

## Migrating older scrills

Move the manual and its metadata into `README.md`; keep agent-facing API and implementation guidance in the entry docstring. Reconcile an existing README rather than overwriting it blindly. There is no legacy fallback or automatic migration. Normal Python imports are not a format gate: an undocumented package may still import from `py`, but is not a valid scrill and supplies no registry version. `read` and `run` require a readable README before proceeding.

## A run's lifetime

A scrill itself has no lifetime: it is a folder of files, not a process — nothing runs between calls, there is nothing to start, stop, or reload, and an edit is live on the next call.

A scrill run is an ordinary process: the command *is* the run — no wrapper, daemon, or supervisor — so its lifetime belongs to whatever started it. In a terminal it ends when you interrupt it; under an agent it lives in the harness's process tree, and what the harness does at session end is the harness's business; under cron or launchd it runs on the scheduler's clock, independent of any session. Scrills never kills, detaches, or supervises a run — it records execution metadata and retains its response. Work that must outlive its starter needs a starter that outlives the session (Scheduled work, below). A child a run spawns in the background is a plain process of its own: the run ends, the child continues, and scrills doesn't track it unless the scrill does.

## Scheduled work

A program scrill slots straight into cron or launchd — one-shot checks, scheduled work. Work that must outlive the session needs exactly that: a timer inside a session dies with the session, and a session-scoped scheduler goes with it.

Every run gets `SCRILLS_CLI` in its environment — the command's own absolute path; spawn nested `scrills` children through it rather than searching PATH for the command. The child's PATH must still provide Python 3.13+ as `python3`, including under cron/launchd. A scheduled job's cwd decides its project layer: cron starts in `$HOME`, launchd in `/`, so a job that needs project scrills must `cd` in first — otherwise only the user layer resolves and the import fails plainly, not through the lost-project notice, which needs inherited layers a scheduler doesn't have. On macOS, scheduled jobs can't read TCC-protected folders (`~/Desktop`, `~/Documents`, `~/Downloads`), even through symlinks — keep the scrills clone outside them; the piped installer's default (`~/.local/share/scrills`) already is.

## Bare python

Some integrations run the venv python directly, skipping the CLI — a harness hook that fires every prompt, say, where a run record each time would be noise. Bare venv python sees only the user layer: the resolver takes its layer list from `$SCRILLS_LAYERS` (pathsep-joined directories, project first) and falls back to the user library when it's unset. We hit this; the fix is to hand the interpreter its layers where the hook fires — hooks run in the project directory: `SCRILLS_LAYERS="$PWD/.scrills:$HOME/.scrills" "$HOME/.scrills/.venv/bin/python" -I -c '…'`. That is today's mechanism, not a promise — if the resolver's input ever changes, this paragraph changes with it, so re-check it when the manual's version moves.

## IDE and linters

The dot in `.scrills` keeps default toolchains out: Pyright/Pylance excludes `**/.*` from project analysis, mypy's crawler skips it, pytest doesn't collect from it. Two known exceptions: **ruff does check it** — add `extend-exclude = [".scrills"]` plus `force-exclude = true` (the latter keeps explicit-path runs out too) to the project's ruff config; and editors still analyze a scrill file *while you have it open*, where a missing-package squiggle means the package is absent from the project's venv, not necessarily from the scrills one.

## Packages

`scrills py` and `scrills run` share one environment (`~/.scrills/.venv`, created on first use). A missing package raises an ordinary `ModuleNotFoundError`, with one stderr line appended naming the fix:

```bash
scrills install httpx
```

pip behind it, arguments pass through (`-U`, `==` pins, `-r`); `scrills where` shows the raw command it stands for. Nothing is installed unless asked.

Both the launching `python3` on PATH and the shared environment must be Python 3.13+; this includes cron/launchd. There is no old-Python bootstrap. If an existing environment is older, remove only `~/.scrills/.venv` (or `$SCRILLS_HOME/.venv`), rerun under 3.13+, and reinstall its packages. Your scrills, state and saved responses stay untouched; scrills does not rebuild automatically.

## Secrets

Read a secret from the environment inside a function, at call time. Don't hardcode it in a scrill, and don't print it — heredoc code passes through the transcript, and printed secrets also persist in response history.
