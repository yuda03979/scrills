# working on scrills

This file is for a coding agent changing this repository. If you want to *use* scrills, read
[`scrills/SKILL.md`](scrills/SKILL.md) instead — that's the manual.

## The shape of the repo

```
scrills/scripts/scrills   the CLI: discovery, gating, dispatch - stdlib only
scrills/scripts/_boot.py  what every py/run child execs onto (registry finalize, the two bodies)
scrills/scripts/_scrills_pth.py  the resolver source, copied into the venv's site-packages
scrills/SKILL.md          the manual agents read; also where the version lives
tests/                    subprocess tests that drive the real CLI
docs/                     the documentation site
install.sh                command symlink plus detected harness skill links, nothing else
```

The command is a symlink into this repo, and the CLI finds `SKILL.md`, `_boot.py` and
`_scrills_pth.py` by its own realpath. Keep `scrills/scripts/` and `scrills/SKILL.md` in that
relationship. The boot and resolver are real files, never source-in-a-string: edit them
directly, lint them, and trust the compile guard in the suite. The run context reaches the boot
as JSON in `SCRILLS_BOOT_CTX` (popped on entry); a `py` snippet reaches it on stdin - argv
carries no code.

## Rules that are not negotiable

- **One folder, standard library only.** Everything under `scrills/scripts/` imports nothing
  outside the stdlib and must stay parseable by **Python 3.9** — a stock launcher (cron, an old
  `/usr/bin/python3`) reads the CLI before anything relaunches onto the venv, and the venv itself
  may have been built by one. Check with
  `python3 -c "import ast, pathlib; [ast.parse(p.read_text(), feature_version=(3,9)) for p in [pathlib.Path('scrills/scripts/scrills'), *pathlib.Path('scrills/scripts').glob('*.py')]]"`.
- **Docstrings are the product.** A scrill's module and function docstrings are its manual —
  `help()` renders them, `scrills list` parses them (a `SKILL.md` beside `__init__.py` is the
  manual when present — the docstring then explains its own file, and a frontmattered one is
  ignored as a manifest, aloud). Never strip a docstring in a scrill you write. Elsewhere,
  prefer clear names over comments; the CLI keeps its explanation in one block at the top of
  the file and none below it.
- **The version lives once**, in `scrills/SKILL.md` frontmatter under `metadata.version`. The CLI
  reads it there; nothing else states a version number. Bump it for anything worth shipping.
- **The frontmatter follows the Agent Skills spec**: only `name`, `description`, `license`,
  `compatibility`, `metadata` and `allowed-tools`, with `name` matching the directory. A test
  enforces it. Scrill *docstrings* deliberately differ — see below.
- **`.scrills/` folders are live code**, not fixtures. `tests/fixtures/ide/` exists only to
  prove editors and linters stay out of them.

## Scrills are not skills, in one deliberate way

A scrill's manual — a `SKILL.md` beside `__init__.py`, or the docstring in the one-file shape — looks like a
SKILL.md and differs on purpose: the name is a **Python identifier**, so `_` where a skill name
would have `-` (the folder name is the import name, and an identifier can't hold a hyphen).
`version` may sit top-level or under `metadata:` as the spec nests it — both are read.

A scrill's manual is never validated as a skill — a skill folder becomes a scrill by adding
`__init__.py`, and anything that exports a scrill as a skill maps `_` → `-` at that boundary.
Don't "fix" scrill names to match the skills spec — only `scrills/SKILL.md` must validate,
because only that file is claimed as a skill.

## Tests

```bash
uv run --with pytest==8.4.2 python -m pytest tests/ -q
```

Expect **129 passed** on Python 3.13; Python 3.9 reports **127 passed, 2 skipped**. The suite drives the real CLI as a subprocess against a session-scoped
scratch `SCRILLS_HOME`, and scrubs inherited `SCRILLS_*` and `TRACEPARENT` so a developer's
environment can't steer it. **Never let a test spend money or make noise.**

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

`docs/manual.md` includes `scrills/SKILL.md` by snippet — one source for the manual. Don't
paste its content into the docs tree. The include skips the file's frontmatter by line offset;
`test_docs_include_offsets` pins the offset to the real block — if it fails, fix the offset,
never the assertion.
