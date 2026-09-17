# working on scrills

This file is for a coding agent changing this repository. If you want to *use* scrills, read
[`scrills/SKILL.md`](scrills/SKILL.md) instead — that's the manual.

## The shape of the repo

```
scrills/scripts/scrills   the entire implementation, one file
scrills/SKILL.md          the manual agents read; also where the version lives
examples/.scrills/        four working scrills, a live project layer
tests/                    subprocess tests that drive the real CLI
docs/                     the documentation site
install.sh                two symlinks, nothing else
```

The command is a symlink into this repo, and the CLI finds `SKILL.md` by walking up from its own
realpath. Keep `scrills/scripts/scrills` and `scrills/SKILL.md` in that relationship.

## Rules that are not negotiable

- **One file, standard library only.** `scrills/scripts/scrills` imports nothing outside the
  stdlib and must stay parseable by **Python 3.9** — a stock launcher (cron, an old `/usr/bin/python3`)
  reads it before anything relaunches onto the venv. Check with
  `python3 -c "import ast; ast.parse(open('scrills/scripts/scrills').read(), feature_version=(3,9))"`.
- **Docstrings are the product.** A scrill's module and function docstrings are its manual —
  `help()` renders them, `scrills list` parses them. Never strip a docstring anywhere under
  `examples/.scrills/` or in a scrill you write. Elsewhere, prefer clear names over comments;
  the CLI keeps its explanation in one block at the top of the file and none below it.
- **The version lives once**, in `scrills/SKILL.md` frontmatter under `metadata.version`. The CLI
  reads it there; nothing else states a version number. Bump it for anything worth shipping.
- **The frontmatter follows the Agent Skills spec**: only `name`, `description`, `license`,
  `compatibility`, `metadata` and `allowed-tools`, with `name` matching the directory. A test
  enforces it. Scrill *docstrings* deliberately differ — see below.
- **`.scrills/` folders are live code**, not fixtures. `examples/.scrills/` is a real project
  layer; `tests/fixtures/ide/` exists to prove editors and linters stay out of it.

## Scrills are not skills, in two deliberate ways

A scrill's docstring frontmatter looks like a SKILL.md and differs on purpose:

- the name is a **Python identifier**, so `_` where a skill name would have `-` (the folder name
  is the import name, and an identifier can't hold a hyphen);
- `version` stays **top-level** in a scrill docstring, not under `metadata`.

No skill tooling ever reads a scrill docstring, so neither costs anything. Anything that exports a
scrill as a skill maps `_` → `-` at that boundary. Don't "fix" scrill docstrings to match the
skills spec — only `scrills/SKILL.md` follows it, because only that file is a skill.

## Tests

```bash
uv run --with pytest==8.4.2 python -m pytest tests/ -q
```

Expect **110 passed, 3 skipped** — the three skips are example tests that need `pypdf` in the
session venv and are covered by live runs instead. The suite drives the real CLI as a subprocess
against a session-scoped scratch `SCRILLS_HOME`, and scrubs inherited `SCRILLS_*` and
`TRACEPARENT` so a developer's environment can't steer it. Example tests run offline behind PATH
stubs for `claude`, `say`, `ioreg`, `terminal-notifier` and `osascript` — **never let a test spend
money or make noise.**

New behaviour gets a test that was seen failing first.

## Proving a change

A green suite is not the whole proof. Run the real command from a real shell too — `scrills list`,
`scrills where`, `scrills ps`, and the verb you touched — and paste what it actually printed. Never
infer an outcome from a log line saying something succeeded.

## Docs

```bash
uvx --with-requirements docs/requirements.txt mkdocs serve
uvx --with-requirements docs/requirements.txt mkdocs build --strict
```

`docs/manual.md` and `docs/examples.md` include `scrills/SKILL.md` and `examples/README.md` by
snippet — one source per file. Don't paste their content into the docs tree.
