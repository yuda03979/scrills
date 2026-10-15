---
name: scrills
description: Python capability library you call from bash - a scrill is script + skill. Call existing scrills when they fit, including one-off requests; save operations worth reusing. Use direct tools and the project's runtime for ordinary development. Read this skill before first use.
compatibility: Needs bash and python3 (3.13 or newer) on macOS or Linux (Debian/Ubuntu also need the matching venv package). The `scrills` command (scrills/scripts/scrills in the repository) goes on PATH or is called by path; no other runtime dependencies.
license: Apache-2.0
metadata:
  version: "0.3.5"
---

# scrills

A scrill is **script + skill**: a Python folder with its own manual. It has two required files: `README.md` for humans and agents, and `__init__.py` for the callable code. The entry's docstrings explain its API. The `scrills` command finds these folders and lets you call or combine them.

## Find, read, call

The `slug` scrill in this example turns "My Title" into `my-title`. It's illustrative, not bundled. Its full source appears under [Make a scrill](#make-a-scrill).

See what's available:

```bash
scrills list
```

This prints a name and description for each scrill. It reads files without running their code. Use a current listing already in context if you have one.

Read your choice before its first use:

```bash
scrills read slug
```

This shows the complete README and entry source, with absolute paths, line counts and end markers. It does not import the scrill or create a Python environment. If your tool clips the output, continue reading the printed paths until you have both files. Follow the imports and references relevant to the call, including anything that happens on import. Learn the inputs and side effects before running it, especially when it writes files, uses the network or starts processes.

You can reuse material already reviewed in this context. Refresh it when the selected library or code changes, or when you need context you no longer have. The README explains the author's intended API; the code shows what it can do. Neither changes what the user or harness has authorized.

Call a function with a short Python snippet:

```bash
scrills py <<'PY'
from scrills import slug
print(slug.slugify("My Title"))
PY
```

This prints `my-title`. Import more scrills in the same snippet if you want to combine their functions.

A scrill with `main()` also runs as a program:

```bash
printf 'My Title\n' | scrills run slug
```

It also prints `my-title`. That's the basic flow: find, read, call.

## How calls work

- `scrills py` takes **code on stdin**, as in the heredoc above. It takes no arguments, including `-c`. Each call starts with fresh Python variables. Top-level `await` works, and the final expression echoes its value like a REPL.
- `py` uses stdin for code, not data. Give functions arguments or file paths. Use `scrills run` for a program that reads data from stdin.
- `scrills run <name> [args...]` passes the arguments and stdin to the scrill's `main()`. It returns the program's exit code. A `--help` after the name is passed to that program; Scrills does not generate its help text. Read the scrill's documented calls.
- A final expression's echo is capped at 8,192 characters. `SCRILLS_ECHO_CAP=0` removes that cap. Print or write the full value when you need it.
- Read sensitive values from the environment inside a function, at call time. Keep them out of snippets and printed output. Snippets enter the transcript, and output is saved.

Python `help()` can clarify an API after review. It imports code and doesn't load the README, so start with `scrills read` instead.

## The two libraries and their Python

Scrills looks in two places:

- **Project:** the nearest `.scrills/` walking up from the caller's working directory. A project can keep its capabilities in version control with its code.
- **User:** `~/.scrills/`, or `$SCRILLS_HOME` when set. These scrills are available across projects.

If both have the same name, the project one wins. This also affects imports inside other scrills. `py` and `run` warn about different implementations sharing a name; `list` shows the shadowed entry. Use `scrills where` to see both locations and the Python interpreter. A child started outside the project loses the project layer. Give it a working directory inside the project when it needs those scrills. Scrills also reports a `.scrills/` owned by another user rather than using it.

All scrills share one Python environment in the user library's `.venv`. That includes project scrills. The environment is made on the first execution or package install. Add a missing package there with `scrills install <package>`; its arguments pass through to pip.

Your working directory and files remain available. Scrills runs Python with `-I`, so project modules and `PYTHONPATH` aren't on its import path. Run project code with the project's own tools, such as `uv run` or its venv. A scrill can launch those tools as child processes and exchange files or process output with them. This separates imports; it does not confine what the code can do.

## Get output back

`py` and `run` save stdout and stderr, then return them synchronously before exit. They first print `scrills: run <id>` on stderr. If the harness clips or loses the response, retrieve the saved bytes **instead of rerunning the operation**:

```bash
scrills history                         # latest 20 saved IDs
scrills history <id>                    # first 8192 bytes of stdout
scrills history <id> --offset 8192 --limit 8192
scrills history <id> --stream stderr
```

Offsets are zero-based bytes within one stream. `--limit` accepts 1–65,536. When more is available, history prints a next-offset command on stderr. Reading history does not run the scrill again or require its Python environment.

History recovers output clipped by the harness. It cannot recover a value already shortened by Scrills' trailing-echo cap, or data the scrill never fetched. An interrupted run may leave partial output and no ID in the response; use `scrills history` to find saved IDs. If saving cannot be prepared, Scrills warns and runs normally without saving. Capture is for batch work, not interactive prompts or live progress. Saved output stays until removed, so printed sensitive values and growing disk use matter.

To check execution rather than output, run `scrills ps`. It shows running calls, unfinished calls reported as `died`, and a last-24-hours summary. Its metadata record includes the exit code, imported scrills and their declared versions; it stores neither code, arguments nor output. Errors record their type and location, not their message. A run is an ordinary process. Scrills records it but does not keep it alive or restart it. Work that must outlive a session needs an independent starter, such as cron or launchd.

## Make a scrill

Put project-specific work in the project's `.scrills/`. Put a capability you use across projects in the user library. The folder name becomes its Python import name.

Here is the complete `slug` example:

```text
.scrills/
  slug/
    README.md
    __init__.py
```

**`slug/README.md`:**

```markdown
---
name: slug
description: "Make URL slugs from titles. Run: scrills run slug"
metadata:
  version: "0.1.0"
---

`slugify(text)` takes a string and returns a lowercase ASCII slug.
For example, `slugify("Hello, World! — Part 2")` returns `"hello-world-part-2"`.

As a program, it reads text from stdin and prints one slug.
The function returns the complete result. No third-party packages, network calls,
file writes or persistent state.
```

**`slug/__init__.py`:**

```python
"""Convert a title with slugify(text), or run main() on stdin."""
import re
import sys


def slugify(text: str) -> str:
    """Return a lowercase ASCII slug, separating other characters with hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main() -> int:
    """Read stdin, print a slug and return the exit code."""
    print(slugify(sys.stdin.read()))
    return 0
```

Once you save both files, the scrill works on the next call. There is nothing to register or restart. Read it with `scrills read slug`, then use either call shown above.

A few conventions keep a scrill easy to use:

- **README is the manual and the only metadata source.** Name the folder with a lowercase Python identifier and match it in `name`. Use `_` where a skill name would use a dash. The description says what it does and includes a run line for a program. Keep one version, preferably under `metadata`. The header accepts one-line text, not arbitrary YAML; quote a description with punctuation as in the example. An optional `compatibility` field can name platform or tool requirements.
- **Start with the caller's contract.** Show what to call, a working example, inputs and return values, dependencies, side effects and state. For paginated or bounded results, say whether the result is complete, partial or of unknown completeness. Say how to get the rest, or that you cannot.
- **Keep the entry readable.** About 100–200 lines is a soft target. For larger scrills, keep README and `__init__.py` at the root and place the rest in useful subfolders. Point to those files from the entry docstring. These are suggestions, not requirements; a small scrill needs no extra structure. Module and function docstrings explain their APIs and guide further reading.
- **Put work inside functions.** Importing a scrill runs its top-level code, even before `run` calls `main()`. Keep the top level to imports, constants and definitions. A program defines `main()`; it can read `sys.argv` or stdin and return an exit code. It needs no `__main__.py`. To label a meaningful non-zero result in the run record, put `EXITS = {1: "no input"}` beside `main()`.
- **Keep assets and state in the right place.** Open files owned by the scrill relative to `__file__`; cwd belongs to the caller. Keep persistent state in `<user library>/.state/<name>/`, respecting `SCRILLS_HOME`. A leading `_` makes a scrill folder an invisible draft. One scrill can import another with `from scrills import <name>`.
- **Declare dependencies if useful.** A PEP 723 `# /// script` block above the entry docstring can document third-party packages. Scrills does not install them automatically.

A sync function called from an awaiting `py` snippet should use threads for concurrency rather than call `asyncio.run()` inside the running loop. An `async def` version is another option. The snippet's final expression runs separately and can behave differently; don't rely on that exception inside a function.

## Move a scrill to the user library

Copy a tested project scrill when you want it in other projects. Avoid a symlink: it would make each project edit live for every session using the user copy. For a **new** name, with neither destination present:

```bash
library="${SCRILLS_HOME:-$HOME/.scrills}"
mkdir -p "$library"
cp -R .scrills/slug "$library/_slug" && mv "$library/_slug" "$library/slug"
```

The `_slug` copy stays hidden during the copy; the final rename publishes the complete folder. Before an upgrade or retry, check both `$library/_slug` and `$library/slug`. Remove the stale staging folder or published copy you mean to replace, then repeat the staged copy. `cp -R` and `mv` put a source **inside** an existing folder rather than replacing it. Bump the README version before copying changed content. A promoted scrill that imports project-only siblings needs those dependencies promoted too.

## Less-common cases

- **Scheduled calls:** Cron usually starts in `$HOME` and launchd in `/`. Set the job's cwd inside the project if it needs project scrills. Each CLI call sets `SCRILLS_CLI` to the command's absolute path for nested calls. The child's PATH still needs Python 3.13+ as `python3`. On macOS, scheduled jobs may lack access to `~/Desktop`, `~/Documents` or `~/Downloads`, even through symlinks; keep scheduled inputs and the clone outside protected folders.
- **Direct venv Python:** An integration can bypass the CLI and its run record. Bare venv Python sees only user scrills by default. If the hook runs in a project directory with `.scrills/`, pass both layers: `library="${SCRILLS_HOME:-$HOME/.scrills}"; SCRILLS_LAYERS="$PWD/.scrills:$library" "$library/.venv/bin/python" -I -c '...'`. This is today's integration pattern, not a stable API promise.
- **Editor diagnostics:** Ruff needs both `extend-exclude = [".scrills"]` and `force-exclude = true` to keep even explicitly named scrill paths out. An open file may still show missing packages from the project's Python environment rather than Scrills' environment.
- **Older scrills:** Move the manual and metadata into `README.md`; keep API guidance in docstrings and reconcile any existing README. Individual scrills do not use `SKILL.md` as a fallback. Python can still import a package without a README, but `read` and `run` require one. The product's own `skills/scrills/SKILL.md` is the teaching skill, not a scrill manual.
- **If `read` fails:** It still shows files it could read. Exit 1 means a file read/decode failure; exit 2 means bad arguments or a missing or ignored name. `help()` imports code, so use it only after reviewing the source.
- **Runtime repair:** Both the launching `python3` and shared venv must be Python 3.13+. For an older or broken venv, remove only `<user library>/.venv`, rerun with a supported Python and reinstall packages. Your library, state and saved output remain.
- **Run details:** Records live in `<user library>/.runs/log.jsonl`. Set `SCRILLS_WHO=<harness>:<session>` for attribution. A valid `TRACEPARENT` joins nested runs into a trace. These are optional; you can call a scrill without them.
