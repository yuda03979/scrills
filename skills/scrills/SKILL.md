---
name: scrills
description: A scrill is a skill that can also carry Python. One folder is knowledge to read, a library to import, a program to run, or all three. The scrills command lists, reads, imports and runs them from bash.
compatibility: Needs bash and python3 (3.13 or newer) on macOS or Linux (Debian/Ubuntu also need the matching venv package). The `scrills` command (scripts/scrills beside this skill) goes on PATH or is called by path; no other runtime dependencies.
license: Apache-2.0
metadata:
  version: "0.3.9"
---

# scrills

A scrill is a skill that can also carry Python. A harness indexes skills and loads one on demand; `scrills list` is that index and `scrills read <name>` is the load. `scrills py` and `scrills run` do what a skill alone cannot: run its Python.

A scrill is a folder with two required files. `README.md` is the manual under a metadata header, `__init__.py` is the entry, and the folder name is the import name. Anything else sits beside them. An entry holding only a docstring makes the scrill knowledge to read, functions make it a library, a `main()` makes it a program, and one folder can be all three.

```text
.scrills/
  notes/                 knowledge only
    README.md            header (name:, description:, metadata: version:), then the manual
    __init__.py          """Reading notes on X. The material is in references/."""
    references/
  communication/         group: no __init__.py
    emails/              library and program
      README.md
      __init__.py        fetch(query) and main()
      lib/
```

```bash
scrills read notes                      # both files, nothing imported; for notes this is the whole use
scrills py <<'PY'                       # one-shot Python; every scrill is importable
from scrills.communication import emails
print(emails.fetch("unread from:support"))
PY
echo "unread from:support" | scrills run communication.emails    # calls main(); argv, stdin and the exit code pass through
```

| Verb | Does |
|---|---|
| `list` | One `- qualified.name: description` line per scrill, sorted and parsed without importing; no group rows. |
| `read <name>` | Both files in full with absolute paths, without importing. |
| `py` | Runs the Python on stdin in a fresh interpreter; the last expression echoes. |
| `run <name> [args]` | Imports the scrill and calls `main()`; without one it exits 2 after the import. |
| `history [<id>]` | The 20 newest saved IDs, or one call's saved stdout (`--stream stderr`, `--offset`, `--limit`). |
| `ps` | Running calls, calls that died, and a summary of the last 24 hours. |
| `install <pkg>` | `pip install` into the shared environment. |
| `where` | Both libraries and the Python in use. |

Groups are namespace folders without `__init__.py`, nested as needed. Use `scrills read communication.emails`, `scrills run communication.emails`, and `from scrills.communication import emails`. README `name` stays `emails`. Discovery stops at a scrill; its internal folders are not separate scrills. Groups cannot be read or run as scrills. Imports between groups are ordinary Python imports.

Two libraries: the project one is the nearest `.scrills/` at or above the working directory, the user one is `~/.scrills/` or `$SCRILLS_HOME`. Both are visible under a project. Namespace groups merge; matching scrills select the project copy as a whole, also inside imports between scrills. A regular package or module blocks a same-named group, even from the user layer. `list` reports collisions; `py` and `run` warn about them. A scrill is live as soon as its two files exist and is still there next session.

A `py` or `run` call returns its stdout and stderr when it ends, not live; the response starts with `scrills: run <id>` on stderr. `history <id>` reads them back without rerunning. Saved output is kept in the user library until deleted by hand and is not redacted. `ps` records execution metadata only, never code, arguments or output.

All scrills share one Python environment in the user library. Python runs with `-I`: only that environment and the two libraries are importable, not the project's own modules or anything on `PYTHONPATH`. Files, the working directory and environment variables stay reachable, and importing a scrill runs its top-level code. Import isolation, not a sandbox.

Reference: [discovery and reading](reference/usage-reference.md#discovery-and-reading) · [calling and inputs](reference/usage-reference.md#calling-and-inputs) · [saved output and history](reference/usage-reference.md#saved-output-and-history) · [run records and lifetime](reference/usage-reference.md#run-records-and-lifetime) · [manuals, layout and composition](reference/usage-reference.md#manuals-layout-and-composition) · [frontmatter and dependencies](reference/usage-reference.md#frontmatter-and-dependencies) · [promotion, scheduling and integrations](reference/usage-reference.md#promotion-scheduling-and-other-integrations).
