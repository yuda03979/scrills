# working on scrills

This file is for a coding agent changing this repository. If you want to *use* scrills, read
[`scrills/SKILL.md`](scrills/SKILL.md) instead — that's the manual.

## The shape of the repo

```
scrills/scripts/scrills   the CLI: discovery, gating, dispatch - stdlib only
scrills/scripts/_boot.py  what every py/run child execs onto (registry finalize, the two bodies)
scrills/scripts/_scrills_pth.py  the resolver source, copied into the venv's site-packages
scrills/scripts/_manifest.py    README metadata/source reading, shared by CLI and boot
scrills/scripts/_history.py     direct output files, synchronous return, ID-based byte reads
scrills/SKILL.md          the manual agents read; also where the version lives
tests/                    subprocess tests that drive the real CLI
docs/                     the documentation site
install.sh                command symlink plus detected harness skill links, nothing else
```

The command is a symlink into this repo, and the CLI finds `SKILL.md`, `_boot.py`,
`_scrills_pth.py`, `_manifest.py` and `_history.py` by its own realpath. Keep `scrills/scripts/` and `scrills/SKILL.md` in that
relationship. The boot and resolver are real files, never source-in-a-string: edit them
directly, lint them, and trust the compile guard in the suite. The run context reaches the boot
as JSON in `SCRILLS_BOOT_CTX` (popped on entry); a `py` snippet reaches it on stdin - argv
carries no code. The shared manifest module is loaded by its absolute installation path,
not by adding a directory to `sys.path` (`-I` excludes sibling imports). Keep one parser:
listing, static reading and registry versions must never interpret README differently.

## Rules that are not negotiable

- **One folder, standard library only, Python 3.13+.** Everything under `scrills/scripts/`
  imports nothing outside the stdlib. Both the launcher and shared venv must meet the minimum;
  no older-interpreter compatibility layer or automatic Python installation. CI runs Python
  3.13 on macOS and Linux, and the suite guards that grammar. Newer Python syntax/APIs within
  this floor are welcome; do not preserve older-Python workarounds.
- **README and docstrings are product surface, with distinct roles.** Every scrill requires
  `README.md` and `__init__.py`. README is the human/agent manual and sole metadata source;
  entry/module/function docstrings guide API use and implementation reading. No SKILL.md or
  docstring-manual fallback. `scrills read` prints both complete files without importing;
  `help()` requires an import and never replaces that initial read. Never strip useful
  docstrings. Elsewhere prefer clear names over comments; the CLI keeps its explanation in
  one block at the top of the file and none below it.
- **The version lives once**, in `scrills/SKILL.md` frontmatter under `metadata.version`. The CLI
  reads it there; nothing else states a version number. Bump it for anything worth shipping.
- **The frontmatter follows the Agent Skills spec**: only `name`, `description`, `license`,
  `compatibility`, `metadata` and `allowed-tools`, with `name` matching the directory. A test
  enforces it. Individual scrill READMEs deliberately differ — see below.
- **`.scrills/` folders are live code**, not fixtures. `tests/fixtures/ide/` exists only to
  prove editors and linters stay out of them.

## Scrill manuals are not native harness skills

A scrill's `README.md` uses Skills-style metadata, but the filename and Python identifier
names are deliberately different. Only the product's `scrills/SKILL.md` is an Agent Skill;
its filename, version source and installation links stay unchanged.

The shared reader consumes one-line text for `name`, `description`, `version`, `compatibility`,
plus direct `metadata.version`. It is not a full YAML parser. Comments and blank lines cannot
hide continuations; quoted strings are decoded according to the documented subset; unsupported
shapes and duplicate consumed keys are invalid, not guessed. A declared top-level version is
authoritative even when empty/invalid (null, never silently rescued by metadata.version).
README read failures never fall back to another file or `__doc__`.

Resolve the winning Python provider before validating README: a project package missing its
manual must be refused, never replaced by a lower user package. Imports themselves remain
native Python, not a format gate or sandbox. Registry versions are sampled from README at
finalization; do not turn this field into an import-time provenance claim.

## Response history

Save only ordinary stdout/stderr under `.results/<id>/` and return those bytes synchronously
from the boot's finalization, before exit. No metadata, alternate echo, helper process or live
copier. Preserve descriptors, native flushes, subprocess/fork behavior and nested return order.
Preparation failure restores streams and warns, then execution continues without saving.

`history` lists recent IDs; `history <id>` reads raw bytes with only --offset/--limit and
--stream stdout|stderr. No paths, line/combined modes, escaping or completion claims. Output may
be partial; execution information belongs to the independent registry. Read without a venv or
user imports. Keep private permissions and existing output; there is no automatic eviction.
The two-root-file scrill layout remains authoring guidance, not enforcement.

## Tests

```bash
uv run --python 3.13 --with pytest==8.4.2 python -m pytest tests/ -q
```

Expect **268 passed** on Python 3.13. The suite drives the real CLI as a subprocess against a session-scoped
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
