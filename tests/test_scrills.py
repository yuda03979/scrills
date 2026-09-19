# Drives the real CLI as a subprocess: a session-scoped user home (one venv build), a fresh
# project directory per test. Run: uv run --with pytest==8.4.2 python -m pytest tests/ -q
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

from conftest import base_env

CLI = str(Path(__file__).resolve().parent.parent / "scrills" / "scripts" / "scrills")
SKILL = Path(__file__).resolve().parent.parent / "scrills" / "SKILL.md"


def scrills(args, home, cwd, stdin=None, extra_env=None):
    env = {**base_env(), "SCRILLS_HOME": str(home), **(extra_env or {})}
    return subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin,
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def log_records(home, name):
    path = Path(home) / ".runs" / "log.jsonl"
    if not path.exists():
        return []
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return [record for record in lines if record.get("name") == name]


def cards(home, name):
    found = []
    for path in (Path(home) / ".runs").glob("*.json"):
        if path.stem.isdigit() and json.loads(path.read_text()).get("name") == name:
            found.append(path)
    return found


def build_wheel(directory):
    wheel = Path(directory) / "tiny_wheel-0.1.0-py3-none-any.whl"
    info = "tiny_wheel-0.1.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("tiny_wheel.py", "VALUE = 42\n")
        archive.writestr(f"{info}/METADATA", "Metadata-Version: 2.1\nName: tiny-wheel\nVersion: 0.1.0\n")
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: scrills-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", f"tiny_wheel.py,,\n{info}/METADATA,,\n{info}/WHEEL,,\n{info}/RECORD,,\n")
    return wheel


def write_scrill(root, name, doc, body=""):
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text(f'"""\n{doc}\n"""\n{textwrap.dedent(body)}')
    return folder


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".scrills").mkdir()
    return tmp_path


SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def test_version(home, project):
    text = SKILL.read_text()
    assert text.startswith("---\n")
    frontmatter = text[4 : text.index("\n---", 4)]
    top = {line.split(":", 1)[0] for line in frontmatter.splitlines() if line[:1].strip()}
    assert top <= SPEC_KEYS, sorted(top - SPEC_KEYS)
    assert SKILL.parent.name == "scrills"
    stated = next(
        line.split(":", 1)[1].strip().strip("\"'")
        for line in frontmatter[frontmatter.index("\nmetadata:") :].splitlines()
        if line[:1].isspace() and line.strip().startswith("version:")
    )
    assert "version:" in text[text.index("\n---", 4) :], "the body decoy the parser must ignore is gone"
    result = scrills(["--version"], home, project)
    assert result.returncode == 0
    assert result.stdout.strip() == stated


def test_py_echo_and_stdout(home, project):
    result = scrills(["py"], home, project, stdin='print("out")\n1 + 1')
    assert result.returncode == 0
    assert result.stdout == "out\n2\n"


def test_py_none_does_not_echo(home, project):
    result = scrills(["py"], home, project, stdin="x = 1")
    assert result.returncode == 0
    assert result.stdout == ""


def test_py_exception(home, project):
    result = scrills(["py"], home, project, stdin='print("before")\nboom')
    assert result.returncode == 1
    assert result.stdout == "before\n"
    assert '"<py>"' in result.stderr and "NameError" in result.stderr
    assert "<string>" not in result.stderr


def test_py_empty_stdin(home, project):
    result = scrills(["py"], home, project, stdin="")
    assert result.returncode == 2
    assert "no code on stdin" in result.stderr


def test_py_no_state(home, project):
    assert scrills(["py"], home, project, stdin="x = 41").returncode == 0
    result = scrills(["py"], home, project, stdin="x + 1")
    assert result.returncode == 1
    assert "NameError" in result.stderr


def test_py_top_level_await(home, project):
    code = "import asyncio\nawait asyncio.sleep(0)\n'awaited'"
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0
    assert result.stdout == "'awaited'\n"


def test_py_async_exit_is_quiet(home, project):
    code = textwrap.dedent(
        """
        import asyncio
        async def forever():
            await asyncio.sleep(60)
        task = asyncio.ensure_future(forever())
        await asyncio.sleep(0)
        'done'
        """
    )
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0
    assert result.stdout == "'done'\n"
    assert "Task was destroyed" not in result.stderr
    assert "RuntimeWarning" not in result.stderr


def test_project_scrill_and_sibling(home, project):
    write_scrill(
        project / ".scrills",
        "greet",
        "---\nname: greet\ndescription: d\nversion: 0.1.0\n---",
        """
        from .words import WORD
        def hello():
            return WORD
        """,
    )
    (project / ".scrills" / "greet" / "words.py").write_text('WORD = "hola"')
    result = scrills(["py"], home, project, stdin="from scrills import greet\ngreet.hello()")
    assert result.returncode == 0
    assert result.stdout == "'hola'\n"


def test_user_scrill(home, project):
    write_scrill(home, "fromuser", "---\nname: fromuser\ndescription: d\nversion: 0.1.0\n---", "VALUE = 7")
    result = scrills(["py"], home, project, stdin="from scrills import fromuser\nfromuser.VALUE")
    assert result.returncode == 0
    assert result.stdout == "7\n"


def test_project_shadows_user(home, project):
    write_scrill(home, "shade", "user layer", "WHO = 'user'")
    write_scrill(project / ".scrills", "shade", "project layer", "WHO = 'project'")
    result = scrills(["py"], home, project, stdin="from scrills import shade\nshade.WHO")
    assert result.stdout == "'project'\n"
    listing = scrills(["list"], home, project)
    assert "- shade: project layer" in listing.stdout
    assert "shade  (user, shadowed by project)" in listing.stdout


def test_scrill_imports_scrill(home, project):
    write_scrill(home, "base", "base", "NUM = 2")
    write_scrill(
        project / ".scrills",
        "double",
        "double",
        """
        from scrills import base
        def value():
            return base.NUM * 2
        """,
    )
    result = scrills(["py"], home, project, stdin="from scrills import double\ndouble.value()")
    assert result.stdout == "4\n"


def test_list_entries_are_skills_shaped(home, project):
    write_scrill(
        project / ".scrills",
        "tool",
        "---\nname: tool\ndescription: does things\nversion: 0.1.0\n---\n\nmore prose",
        "def main():\n    return 0",
    )
    write_scrill(project / ".scrills", "_draft", "hidden")
    result = scrills(["list"], home, project)
    lines = result.stdout.splitlines()
    assert "- tool: does things" in lines
    assert "runs standalone" not in result.stdout
    assert not any(line.strip() == "---" for line in lines)
    assert not any(line.strip().startswith(("name:", "version:")) for line in lines)
    assert "more prose" not in result.stdout
    assert "_draft" not in result.stdout


def test_run_passthrough(home, project):
    write_scrill(
        project / ".scrills",
        "shout",
        "shout",
        """
        import sys
        def main():
            data = sys.stdin.read().strip()
            print(f"{data} {' '.join(sys.argv[1:])}".upper())
            return 3
        """,
    )
    result = scrills(["run", "shout", "a", "b"], home, project, stdin="hi")
    assert result.returncode == 3
    assert result.stdout == "HI A B\n"


def test_run_main_none_is_zero(home, project):
    write_scrill(project / ".scrills", "quiet", "quiet", "def main():\n    pass")
    assert scrills(["run", "quiet"], home, project).returncode == 0


def test_run_async_main(home, project):
    write_scrill(
        project / ".scrills",
        "aio",
        "aio",
        """
        import asyncio
        async def main():
            await asyncio.sleep(0)
            print("awaited main")
            return 5
        """,
    )
    result = scrills(["run", "aio"], home, project)
    assert result.returncode == 5
    assert result.stdout == "awaited main\n"
    listing = scrills(["list"], home, project)
    assert "- aio: aio" in listing.stdout


def test_run_reexported_main(home, project):
    write_scrill(
        project / ".scrills",
        "facade",
        "facade",
        "from .cli import main",
    )
    (project / ".scrills" / "facade" / "cli.py").write_text("def main():\n    print('via cli')\n    return 0\n")
    result = scrills(["run", "facade"], home, project)
    assert result.returncode == 0
    assert result.stdout == "via cli\n"
    listing = scrills(["list"], home, project)
    assert "- facade: facade" in listing.stdout


def test_run_new_syntax_scrill(home, project):
    write_scrill(
        project / ".scrills",
        "matcher",
        "matcher",
        """
        import sys
        def main():
            match len(sys.argv):
                case 1:
                    print("one")
                case _:
                    print("many")
            return 0
        """,
    )
    listing = scrills(["list"], home, project)
    assert "- matcher: matcher" in listing.stdout
    assert "doesn't parse" not in listing.stdout
    result = scrills(["run", "matcher"], home, project)
    assert result.returncode == 0
    assert result.stdout == "one\n"


def test_run_syntax_error_is_truthful(home, project):
    write_scrill(project / ".scrills", "broken", "broken", "def main(:\n    pass")
    result = scrills(["run", "broken"], home, project)
    assert result.returncode == 1
    assert "SyntaxError" in result.stderr
    assert "defines no main()" not in result.stderr
    listing = scrills(["list"], home, project)
    assert "doesn't parse" in listing.stdout


def test_source_decoding_matches_python(home, project):
    bom = project / ".scrills" / "bom"
    bom.mkdir(parents=True)
    text = '"""\n---\nname: bom\ndescription: opens with a byte order mark\nversion: 0.1.0\n---\n"""\ndef main():\n    print("bom ran")\n    return 0\n'
    (bom / "__init__.py").write_bytes(text.encode("utf-8-sig"))
    legacy = project / ".scrills" / "legacy"
    legacy.mkdir(parents=True)
    declared = '# -*- coding: latin-1 -*-\n"""\n---\nname: legacy\ndescription: café special\nversion: 0.1.0\n---\n"""\n'
    (legacy / "__init__.py").write_bytes(declared.encode("latin-1"))
    listing = scrills(["list"], home, project)
    assert "doesn't parse" not in listing.stdout
    assert "- bom: opens with a byte order mark" in listing.stdout
    assert "- legacy: café special" in listing.stdout
    ran = scrills(["run", "bom"], home, project)
    assert ran.returncode == 0
    assert ran.stdout == "bom ran\n"


def test_run_accepts_guarded_and_indirect_main(home, project):
    write_scrill(
        project / ".scrills",
        "guarded",
        "guarded",
        """
        import sys
        if sys.platform != "emptiness":
            def main():
                print("guarded ran")
                return 0
        """,
    )
    write_scrill(
        project / ".scrills",
        "tupled",
        "tupled",
        """
        def _real():
            print("tupled ran")
            return 0
        main, extra = _real, 1
        """,
    )
    write_scrill(project / ".scrills", "starred", "starred", "from .cli import *")
    (project / ".scrills" / "starred" / "cli.py").write_text("def main():\n    print('starred ran')\n    return 0\n")
    write_scrill(
        project / ".scrills",
        "dynamic",
        "dynamic",
        """
        def __getattr__(name):
            if name == "main":
                return lambda: print("dynamic ran") or 0
            raise AttributeError(name)
        """,
    )
    for name in ("guarded", "tupled", "starred", "dynamic"):
        result = scrills(["run", name], home, project)
        assert result.returncode == 0, (name, result.stderr)
        assert f"{name} ran" in result.stdout


def test_run_accepts_dynamic_main(home, project):
    write_scrill(
        project / ".scrills",
        "globaled",
        "globaled",
        """
        def _setup():
            global main
            def main():
                print("globaled ran")
                return 0
        _setup()
        """,
    )
    write_scrill(
        project / ".scrills",
        "dictset",
        "dictset",
        """
        def _real():
            print("dictset ran")
            return 0
        globals()["main"] = _real
        """,
    )
    write_scrill(
        project / ".scrills",
        "headered",
        "headered",
        """
        def _real():
            print("headered ran")
            return 0
        def _unused(bound=(main := _real)):
            return bound
        """,
    )
    for name in ("globaled", "dictset", "headered"):
        result = scrills(["run", name], home, project)
        assert result.returncode == 0, (name, result.stderr)
        assert f"{name} ran" in result.stdout


def test_list_entry_fallbacks_without_frontmatter(home, project):
    write_scrill(project / ".scrills", "prosey", "first line of prose\nsecond line")
    silent = project / ".scrills" / "silent"
    silent.mkdir(parents=True)
    (silent / "__init__.py").write_text("VALUE = 1\n")
    listing = scrills(["list"], home, project)
    assert "- prosey: first line of prose" in listing.stdout
    assert "second line" not in listing.stdout
    assert "- silent: (no module docstring, so it announces nothing)" in listing.stdout


def test_other_uid_project_layer_is_ignored(home, tmp_path):
    (tmp_path / ".scrills").symlink_to("/usr")
    where = scrills(["where"], home, tmp_path)
    assert "project: none" in where.stdout
    assert "owned by uid 0" in where.stdout
    result = scrills(["py"], home, tmp_path, stdin="1")
    assert result.returncode == 0
    assert "owned by uid 0" in result.stderr
    assert result.stdout == "1\n"


def test_shadow_notice_on_py_and_run(home, project):
    write_scrill(home, "clash", "user side", "WHO = 'user'")
    write_scrill(project / ".scrills", "clash", "project side", "WHO = 'project'\ndef main():\n    print(WHO)\n    return 0")
    result = scrills(["py"], home, project, stdin="1")
    assert "shadow" in result.stderr
    assert "clash" in result.stderr
    ran = scrills(["run", "clash"], home, project)
    assert ran.returncode == 0
    assert "clash" in ran.stderr
    (project / ".scrills" / "clash" / "__init__.py").unlink()
    (project / ".scrills" / "clash").rmdir()
    (project / ".scrills" / "clash").symlink_to(Path(home) / "clash")
    linked = scrills(["py"], home, project, stdin="1")
    assert "shadow" not in linked.stderr


def test_list_notices_missing_and_unclosed_frontmatter(home, project):
    write_scrill(project / ".scrills", "nofm", "just prose, no fences")
    write_scrill(project / ".scrills", "unclosed", "---\nname: unclosed\ndescription: d")
    write_scrill(project / ".scrills", "quoted", "---\nname: 'quoted'\ndescription: d\nversion: 0.1.0\n---")
    result = scrills(["list"], home, project)
    assert result.returncode == 0
    assert "(no frontmatter" in result.stdout
    assert "- unclosed:\n" in result.stdout
    assert "never closes" in result.stdout
    assert "differs from folder" not in result.stdout


def test_list_reports_keyword_and_stray_module(home, project):
    write_scrill(project / ".scrills", "class", "---\nname: class\ndescription: d\nversion: 0.1.0\n---", "def main():\n    print('kw ran')\n    return 0")
    (project / ".scrills" / "loose.py").write_text("KIND = 'stray'\n")
    write_scrill(home, "loose", "loose", "KIND = 'package'")
    listing = scrills(["list"], home, project)
    assert "python keyword" in listing.stdout
    assert "loose.py" in listing.stdout
    assert "shadows the user scrill loose" in listing.stdout
    probe = scrills(["py"], home, project, stdin="from scrills import loose\nloose.KIND")
    assert probe.returncode == 0
    assert probe.stdout == "'stray'\n"
    assert "shadow" in probe.stderr
    assert "loose.py" in probe.stderr
    ran = scrills(["run", "class"], home, project)
    assert ran.returncode == 0
    assert ran.stdout == "kw ran\n"
    assert "loose.py" in ran.stderr


def test_list_shows_strays_alone(tmp_path):
    project = tmp_path / "proj"
    (project / ".scrills").mkdir(parents=True)
    (project / ".scrills" / "orphan.py").write_text("VALUE = 1\n")
    result = scrills(["list"], tmp_path / "no-home", project)
    assert result.returncode == 0
    assert "no scrills yet" in result.stdout
    assert "orphan.py" in result.stdout


def test_run_no_main(home, project):
    write_scrill(project / ".scrills", "libonly", "libonly", "print('imported')\nVALUE = 1")
    result = scrills(["run", "libonly"], home, project)
    assert result.returncode == 2
    assert "defines no main()" in result.stderr
    assert "imported" not in result.stdout


def test_run_unknown(home, project):
    write_scrill(project / ".scrills", "known", "known")
    result = scrills(["run", "nope"], home, project)
    assert result.returncode == 2
    assert "no scrill named nope" in result.stderr
    assert "known" in result.stderr


def test_run_cwd_decoy_does_not_shadow(home, project):
    write_scrill(
        project / ".scrills",
        "pkg",
        "pkg",
        """
        from .dep import REAL
        def main():
            print(REAL)
            return 0
        """,
    )
    (project / ".scrills" / "pkg" / "dep.py").write_text('REAL = "sibling"')
    (project / "dep.py").write_text('REAL = "decoy"')
    result = scrills(["run", "pkg"], home, project)
    assert result.returncode == 0
    assert result.stdout == "sibling\n"


def test_run_stdlib_decoy_does_not_shadow(home, project):
    write_scrill(
        project / ".scrills",
        "jsonuser",
        "jsonuser",
        """
        import json
        def main():
            print(json.dumps({"real": True}))
            return 0
        """,
    )
    (project / "json.py").write_text('def dumps(*a, **k):\n    return "DECOY"\n')
    result = scrills(["run", "jsonuser"], home, project)
    assert result.returncode == 0
    assert result.stdout == '{"real": true}\n'
    record = log_records(home, "jsonuser")[-1]
    assert record["status"] == "ok"


def test_py_stdlib_decoy_does_not_shadow(home, project):
    (project / "json.py").write_text('def dumps(*a, **k):\n    return "DECOY"\n')
    result = scrills(["py"], home, project, stdin='import json\njson.dumps([1])')
    assert result.returncode == 0
    assert result.stdout == "'[1]'\n"


def test_py_cwd_import_blocked(home, project):
    (project / "decoymod.py").write_text("VALUE = 1\n")
    result = scrills(["py"], home, project, stdin="import decoymod")
    assert result.returncode == 1
    assert "ModuleNotFoundError" in result.stderr


def test_py_pickles_snippet_class(home, project):
    code = textwrap.dedent(
        """
        import pickle
        class Point:
            def __init__(self, x):
                self.x = x
        pickle.loads(pickle.dumps(Point(3))).x
        """
    )
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "3\n"


def test_process_pool_over_scrill(home, project):
    write_scrill(project / ".scrills", "poolable", "poolable", "def double(n):\n    return n * 2")
    code = textwrap.dedent(
        """
        from concurrent.futures import ProcessPoolExecutor
        from scrills import poolable
        with ProcessPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(poolable.double, [1, 2, 3]))
        results
        """
    )
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[2, 4, 6]\n"


def test_spawned_workers_write_no_pycache_into_scrills(home, project):
    write_scrill(project / ".scrills", "spawnable", "spawnable", "def double(n):\n    return n * 2")
    code = textwrap.dedent(
        """
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from scrills import spawnable
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as pool:
            results = list(pool.map(spawnable.double, [1, 2, 3]))
        results
        """
    )
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[2, 4, 6]\n"
    assert not list((project / ".scrills").rglob("__pycache__"))


def test_state_dir_is_self_gitignored(home, project):
    assert scrills(["py"], home, project, stdin="1").returncode == 0
    ignore = Path(home) / ".state" / ".gitignore"
    assert ignore.exists()
    assert ignore.read_text() == "*\n"


def test_broken_venv_says_rebuild(tmp_path):
    fresh = tmp_path / "brokenhome"
    bin_dir = fresh / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(tmp_path / "no-such-python")
    result = scrills(["py"], fresh, tmp_path, stdin="1")
    assert result.returncode == 1
    assert f"rm -rf {fresh / '.venv'}" in result.stderr
    assert "no longer exists" in result.stderr


def test_site_packages_picked_by_pyvenv_cfg(tmp_path):
    fresh = tmp_path / "cfghome"
    venv = fresh / ".venv"
    for version in ("3.9", "3.13"):
        (venv / "lib" / f"python{version}" / "site-packages").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/local/bin\nversion = 3.13.7\n")
    result = scrills(["where"], fresh, tmp_path)
    assert result.returncode == 0
    assert f"resolver: {venv / 'lib' / 'python3.13' / 'site-packages'}" in result.stdout


def test_resolver_ships_start_file(home, project):
    assert scrills(["py"], home, project, stdin="1").returncode == 0
    result = scrills(["where"], home, project)
    site = re.search(r"^resolver: (\S+)$", result.stdout, re.M)
    assert site is not None, result.stdout
    site_dir = Path(site.group(1))
    assert (site_dir / "scrills.start").read_text() == "_scrills_pth:install\n"
    assert (site_dir / "scrills.pth").read_text() == "import _scrills_pth; _scrills_pth.install()\n"
    assert "def install" in (site_dir / "_scrills_pth.py").read_text()


def test_echo_cap_default(home, project):
    result = scrills(["py"], home, project, stdin="'x' * 20000")
    assert result.returncode == 0
    assert len(result.stdout) < 9000
    assert "… (+" in result.stdout
    assert "print it or write it to a file" in result.stdout


def test_echo_cap_env(home, project):
    uncapped = scrills(["py"], home, project, stdin="'x' * 20000", extra_env={"SCRILLS_ECHO_CAP": "0"})
    assert uncapped.returncode == 0
    assert len(uncapped.stdout) > 20000
    assert "… (+" not in uncapped.stdout
    tiny = scrills(["py"], home, project, stdin="'x' * 20000", extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert tiny.returncode == 0
    assert len(tiny.stdout) < 200
    assert "… (+" in tiny.stdout


def test_install_no_args(home, project):
    result = scrills(["install"], home, project)
    assert result.returncode == 2
    assert "usage: scrills install" in result.stderr


def test_install_offline_wheel(home, project):
    wheel = build_wheel(project)
    result = scrills(["install", "--no-index", str(wheel)], home, project)
    assert result.returncode == 0, result.stderr
    check = scrills(["py"], home, project, stdin="import tiny_wheel\ntiny_wheel.VALUE")
    assert check.returncode == 0, check.stderr
    assert check.stdout == "42\n"


def test_pep723_block_keeps_docstring(home, project):
    folder = project / ".scrills" / "blocky"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(
        "# /// script\n"
        '# dependencies = ["httpx"]\n'
        "# ///\n"
        '"""\n---\nname: blocky\ndescription: declares deps\nversion: 0.1.0\n---\n"""\n'
        "def main():\n"
        "    print('blocky ran')\n"
        "    return 0\n"
    )
    listing = scrills(["list"], home, project)
    assert "- blocky: declares deps" in listing.stdout
    result = scrills(["run", "blocky"], home, project)
    assert result.returncode == 0
    assert result.stdout == "blocky ran\n"


def test_where(home, project):
    result = scrills(["where"], home, project)
    assert f"project: {project / '.scrills'}" in result.stdout
    assert f"user library: {home}" in result.stdout
    assert "packages: scrills install <package>" in result.stdout
    assert "-m pip install" in result.stdout


def test_where_no_project(home, tmp_path):
    result = scrills(["where"], home, tmp_path)
    assert "project: none" in result.stdout


def test_default_user_library_is_never_a_project_layer(home, tmp_path):
    fake_home = tmp_path / "fakehome"
    (fake_home / ".scrills").mkdir(parents=True)
    inside = fake_home / "somewhere"
    inside.mkdir()
    result = scrills(["where"], home, inside, extra_env={"HOME": str(fake_home)})
    assert "project: none" in result.stdout


def test_unknown_verb(home, project):
    result = scrills(["dance"], home, project)
    assert result.returncode == 2
    assert "unknown verb" in result.stderr


def test_run_records_ok(home, project):
    write_scrill(project / ".scrills", "okay", "okay", "def main():\n    return 0")
    result = scrills(["run", "okay"], home, project)
    assert result.returncode == 0
    record = log_records(home, "okay")[-1]
    assert record["verb"] == "run"
    assert record["status"] == "ok"
    assert record["exit"] == 0
    assert record["layer"] == "project"
    assert record["cwd"] == str(project)
    assert record["ms"] >= 0
    assert not cards(home, "okay")


def test_run_records_error(home, project):
    write_scrill(project / ".scrills", "grumpy", "grumpy", "def main():\n    return 4")
    result = scrills(["run", "grumpy"], home, project)
    assert result.returncode == 4
    record = log_records(home, "grumpy")[-1]
    assert record["status"] == "error"
    assert record["exit"] == 4
    assert not cards(home, "grumpy")


def test_py_records_and_who(home, project):
    result = scrills(["py"], home, project, stdin="1", extra_env={"SCRILLS_WHO": "test:sess"})
    assert result.returncode == 0
    record = log_records(home, "py")[-1]
    assert record["verb"] == "py"
    assert record["status"] == "ok"
    assert record["who"] == "test:sess"
    assert not cards(home, "py")


def test_ps_records_died(home, project):
    ghost = subprocess.Popen([sys.executable, "-c", "pass"])
    ghost.wait()
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    card = {
        "verb": "run",
        "name": "ghost",
        "pid": ghost.pid,
        "cwd": str(project),
        "who": "test",
        "started": "2026-09-16T03:12:00+03:00",
        "layer": "project",
    }
    (runs / f"{ghost.pid}.json").write_text(json.dumps(card))
    result = scrills(["ps"], home, project)
    assert result.returncode == 0
    assert "ghost" in result.stdout
    assert "died" in result.stdout
    assert not (runs / f"{ghost.pid}.json").exists()
    assert log_records(home, "ghost")[-1]["status"] == "died"


def test_ps_shows_running_then_died(home, project):
    write_scrill(project / ".scrills", "sleeper", "sleeper", "import time\ndef main():\n    time.sleep(30)\n    return 0")
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    child = subprocess.Popen(
        [sys.executable, CLI, "run", "sleeper"],
        env=env,
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        card = Path(home) / ".runs" / f"{child.pid}.json"
        while not card.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert card.exists()
        result = scrills(["ps"], home, project)
        assert "running:" in result.stdout
        assert "sleeper" in result.stdout
        assert f"pid {child.pid}" in result.stdout
    finally:
        child.kill()
        child.wait()
    result = scrills(["ps"], home, project)
    assert log_records(home, "sleeper")[-1]["status"] == "died"
    assert not cards(home, "sleeper")


def test_py_sigkill_records_died(home, project):
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    child = subprocess.Popen(
        [sys.executable, CLI, "py"],
        env=env,
        cwd=str(project),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert child.stdin is not None
    child.stdin.write("import time\ntime.sleep(30)\n")
    child.stdin.close()
    try:
        deadline = time.monotonic() + 10
        card = Path(home) / ".runs" / f"{child.pid}.json"
        while not card.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert card.exists()
    finally:
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
    assert child.returncode == -signal.SIGKILL
    scrills(["ps"], home, project)
    assert log_records(home, "py")[-1]["status"] == "died"


def test_ps_recycled_pid_shows_died_and_leaves_the_stranger_alone(home, project):
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        runs = Path(home) / ".runs"
        runs.mkdir(exist_ok=True)
        card = {
            "verb": "py",
            "name": "py",
            "pid": stranger.pid,
            "cwd": str(project),
            "who": "test",
            "started": "2026-09-16T03:12:00+03:00",
        }
        (runs / f"{stranger.pid}.json").write_text(json.dumps(card))
        result = scrills(["ps"], home, project)
        assert result.returncode == 0
        assert "running: nothing" in result.stdout
        assert "died" in result.stdout
        assert not (runs / f"{stranger.pid}.json").exists()
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()


def test_killed_run_is_not_kept_running_by_a_shell_child(home, project):
    write_scrill(
        project / ".scrills",
        "spawner",
        "spawner",
        """
        import os, sys, time
        def main():
            os.system("sleep 30 & echo $! > " + sys.argv[1])
            time.sleep(30)
            return 0
        """,
    )
    pidfile = project / "orphan.pid"
    run = subprocess.Popen(
        [sys.executable, CLI, "run", "spawner", str(pidfile)],
        env={**base_env(), "SCRILLS_HOME": str(home)},
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    orphan = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if pidfile.exists() and pidfile.read_text().strip():
                orphan = int(pidfile.read_text())
                break
            time.sleep(0.05)
        assert orphan is not None
        os.kill(run.pid, signal.SIGKILL)
        run.wait()
        result = scrills(["ps"], home, project)
        assert "running: nothing" in result.stdout
        assert log_records(home, "spawner")[-1]["status"] == "died"
    finally:
        if run.poll() is None:
            run.kill()
            run.wait()
        if orphan is not None:
            try:
                os.kill(orphan, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_forked_child_does_not_finalize_the_record(home, project):
    before = len(log_records(home, "py"))
    snippet = textwrap.dedent(
        """
        import os, sys
        pid = os.fork()
        if pid == 0:
            sys.exit(0)
        os.waitpid(pid, 0)
        print("parent done")
        """
    )
    result = scrills(["py"], home, project, stdin=snippet)
    assert result.returncode == 0
    assert "parent done" in result.stdout
    records = log_records(home, "py")
    assert len(records) == before + 1
    assert records[-1]["status"] == "ok"
    assert not cards(home, "py")


def test_log_records_imported_scrills(home, project):
    write_scrill(project / ".scrills", "ia", "ia", "VALUE = 1")
    write_scrill(project / ".scrills", "ib", "---\nname: ib\ndescription: d\nversion: 0.9.9\n---", "VALUE = 2")
    write_scrill(project / ".scrills", "chain", "chain", "from scrills import ia\ndef main():\n    return 0")
    both = scrills(["py"], home, project, stdin="from scrills import ib, ia\nia.VALUE + ib.VALUE")
    assert both.returncode == 0
    assert log_records(home, "py")[-1]["imported"] == {"ia": None, "ib": "0.9.9"}
    none = scrills(["py"], home, project, stdin="1 + 1")
    assert none.returncode == 0
    assert "imported" not in log_records(home, "py")[-1]
    ran = scrills(["run", "chain"], home, project)
    assert ran.returncode == 0
    assert log_records(home, "chain")[-1]["imported"] == {"chain": None, "ia": None}


def test_run_records_have_ids(home, project):
    write_scrill(project / ".scrills", "traced", "traced", "def main():\n    return 0")
    assert scrills(["run", "traced"], home, project).returncode == 0
    assert scrills(["run", "traced"], home, project).returncode == 0
    first, second = log_records(home, "traced")[-2:]
    for record in (first, second):
        assert re.fullmatch(r"[0-9a-f]{16}", record["id"])
        assert re.fullmatch(r"[0-9a-f]{32}", record["trace"])
        assert "parent" not in record
    assert first["id"] != second["id"]
    assert first["trace"] != second["trace"]


def test_traceparent_inherited_and_propagated(home, project):
    given = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"
    result = scrills(
        ["py"],
        home,
        project,
        stdin="import os\nprint(os.environ['TRACEPARENT'])",
        extra_env={"TRACEPARENT": given},
    )
    assert result.returncode == 0
    record = log_records(home, "py")[-1]
    assert record["trace"] == "ab" * 16
    assert record["parent"] == "cd" * 8
    version, trace, span, flags = result.stdout.strip().split("-")
    assert (version, trace, span, flags) == ("00", "ab" * 16, record["id"], "01")
    for broken in ("garbage", "00-" + "0" * 32 + "-" + "cd" * 8 + "-01"):
        assert scrills(["py"], home, project, stdin="1", extra_env={"TRACEPARENT": broken}).returncode == 0
        record = log_records(home, "py")[-1]
        assert record["trace"] != "ab" * 16 and re.fullmatch(r"[0-9a-f]{32}", record["trace"])
        assert "parent" not in record


def test_scrills_cli_exported_to_runs(home, project):
    result = scrills(["py"], home, project, stdin="import os\nprint(os.environ['SCRILLS_CLI'])")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == os.path.realpath(CLI)
    write_scrill(
        project / ".scrills",
        "reenter",
        "---\nname: reenter\ndescription: d\nversion: 0.1.0\n---",
        """
        import os
        import re
        import subprocess

        def main():
            cli = os.environ["SCRILLS_CLI"]
            print(cli)
            bare = {**os.environ, "PATH": "/usr/bin:/bin"}
            probe = subprocess.run([cli, "--version"], capture_output=True, text=True, env=bare)
            print(probe.stdout.strip())
            if not re.fullmatch(r"\\d+\\.\\d+\\.\\d+", probe.stdout.strip()):
                return 1
            return probe.returncode
        """,
    )
    result = scrills(["run", "reenter"], home, project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0] == os.path.realpath(CLI)


def test_error_type_and_location(home, project):
    write_scrill(
        project / ".scrills",
        "deep",
        "deep",
        """
        import json
        def main():
            json.loads("SECRET-DETAIL")
            return 0
        """,
    )
    result = scrills(["run", "deep"], home, project)
    assert result.returncode == 1
    record = log_records(home, "deep")[-1]
    assert record["status"] == "error"
    assert record["error_type"] == "JSONDecodeError"
    assert f"{Path('deep') / '__init__.py'}:" in record["error_at"]
    assert "decoder.py" not in record["error_at"]
    assert "SECRET-DETAIL" not in json.dumps(record)
    snippet = scrills(["py"], home, project, stdin="x = 1\nboom")
    assert snippet.returncode == 1
    record = log_records(home, "py")[-1]
    assert record["status"] == "error"
    assert record["error_type"] == "NameError"
    assert record["error_at"] == "<py>:2"


def test_syntax_error_locations(home, project):
    folder = project / ".scrills" / "badsyntax"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("VALUE = 1\ndef main(:\n    pass\n")
    (folder / "helper.py").write_text("OK = 1\nOK = = 2\n")
    snippet = scrills(["py"], home, project, stdin="x = (\n")
    assert snippet.returncode == 1
    record = log_records(home, "py")[-1]
    assert record["error_type"] == "SyntaxError"
    assert record["error_at"] == "<py>:1"
    ran = scrills(["run", "badsyntax"], home, project)
    assert ran.returncode == 1
    record = log_records(home, "badsyntax")[-1]
    assert record["error_type"] == "SyntaxError"
    assert record["error_at"] == f"{folder / '__init__.py'}:2"
    imported = scrills(["py"], home, project, stdin="from scrills import badsyntax")
    assert imported.returncode == 1
    assert log_records(home, "py")[-1]["error_at"] == f"{folder / '__init__.py'}:2"
    (folder / "__init__.py").write_text("from .helper import OK\n")
    sibling = scrills(["py"], home, project, stdin="from scrills import badsyntax")
    assert sibling.returncode == 1
    assert log_records(home, "py")[-1]["error_at"] == f"{folder / 'helper.py'}:2"


def test_error_at_skips_plumbing(home, project):
    folder = write_scrill(project / ".scrills", "locked", "locked", "def main():\n    return 0")
    entry = folder / "__init__.py"
    mode = entry.stat().st_mode
    entry.chmod(0o000)
    try:
        result = scrills(["run", "locked"], home, project)
    finally:
        entry.chmod(mode)
    assert result.returncode == 1
    record = log_records(home, "locked")[-1]
    assert record["error_type"] == "PermissionError"
    assert not record["error_at"].startswith("<"), record["error_at"]


def test_data_parse_syntax_error_stays_at_the_caller(home, project):
    folder = write_scrill(
        project / ".scrills",
        "parser",
        "parser",
        """
        import ast
        def main():
            ast.parse("1 +")
            return 0
        """,
    )
    result = scrills(["run", "parser"], home, project)
    assert result.returncode == 1
    record = log_records(home, "parser")[-1]
    assert record["error_type"] == "SyntaxError"
    assert record["error_at"].startswith(f"{folder / '__init__.py'}:"), record["error_at"]


def test_exits_names_outcomes(home, project):
    write_scrill(
        project / ".scrills",
        "answers",
        "answers",
        """
        import sys
        EXITS = {0: "zero", 1: "no answer", 2: "usage"}
        def main():
            return int(sys.argv[1])
        """,
    )
    write_scrill(
        project / ".scrills",
        "quitter",
        "quitter",
        """
        import sys
        EXITS = {7: "gave up"}
        def main():
            sys.exit(7)
        """,
    )
    write_scrill(
        project / ".scrills",
        "misnamed",
        "misnamed",
        """
        EXITS = ["not", "a", "dict"]
        def main():
            return 1
        """,
    )
    write_scrill(
        project / ".scrills",
        "thrower",
        "thrower",
        """
        EXITS = {1: "named"}
        def main():
            raise ValueError("boom")
        """,
    )
    assert scrills(["run", "answers", "1"], home, project).returncode == 1
    record = log_records(home, "answers")[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "no answer"
    assert scrills(["run", "answers", "3"], home, project).returncode == 3
    record = log_records(home, "answers")[-1]
    assert record["status"] == "error"
    assert "outcome" not in record
    assert scrills(["run", "answers", "0"], home, project).returncode == 0
    record = log_records(home, "answers")[-1]
    assert record["status"] == "ok"
    assert "outcome" not in record
    assert scrills(["run", "quitter"], home, project).returncode == 7
    record = log_records(home, "quitter")[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "gave up"
    assert record["exit"] == 7
    assert scrills(["run", "misnamed"], home, project).returncode == 1
    assert log_records(home, "misnamed")[-1]["status"] == "error"
    assert scrills(["run", "thrower"], home, project).returncode == 1
    record = log_records(home, "thrower")[-1]
    assert record["status"] == "error"
    assert record["error_type"] == "ValueError"
    assert "outcome" not in record
    assert "boom" not in json.dumps(record)


def test_exits_can_never_write_core_status(home, project):
    write_scrill(
        project / ".scrills",
        "liar",
        "liar",
        """
        import sys
        EXITS = {1: "ok", 2: "died"}
        def main():
            return int(sys.argv[1])
        """,
    )
    for code, name in ((1, "ok"), (2, "died")):
        assert scrills(["run", "liar", str(code)], home, project).returncode == code
        record = log_records(home, "liar")[-1]
        assert record["status"] == "outcome"
        assert record["outcome"] == name


def test_exit_codes_record_what_the_shell_sees(home, project):
    write_scrill(
        project / ".scrills",
        "wrapper",
        "wrapper",
        """
        import sys
        EXITS = {1: "wrapped"}
        def main():
            sys.exit(int(sys.argv[1]))
        """,
    )
    assert scrills(["run", "wrapper", "256"], home, project).returncode == 0
    record = log_records(home, "wrapper")[-1]
    assert record["status"] == "ok"
    assert record["exit"] == 0
    assert scrills(["run", "wrapper", "257"], home, project).returncode == 1
    record = log_records(home, "wrapper")[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "wrapped"
    assert record["exit"] == 1
    result = scrills(["py"], home, project, stdin="import sys\nsys.exit(-1)")
    assert result.returncode == 255
    record = log_records(home, "py")[-1]
    assert record["status"] == "error"
    assert record["exit"] == 255


def test_exits_only_names_what_main_chose(home, project):
    write_scrill(
        project / ".scrills",
        "fatal",
        "fatal",
        """
        import sys
        EXITS = {1: "no answer"}
        def main():
            sys.exit("fatal: broken config")
        """,
    )
    write_scrill(
        project / ".scrills",
        "returner",
        "returner",
        """
        EXITS = {1: "no answer"}
        def main():
            return "something went wrong"
        """,
    )
    write_scrill(
        project / ".scrills",
        "noentry",
        "noentry",
        """
        EXITS = {2: "usage"}
        main = None
        """,
    )
    for name in ("fatal", "returner"):
        assert scrills(["run", name], home, project).returncode == 1
        record = log_records(home, name)[-1]
        assert record["status"] == "error", name
        assert "outcome" not in record, name
    assert scrills(["run", "noentry"], home, project).returncode == 2
    record = log_records(home, "noentry")[-1]
    assert record["status"] == "error"
    assert "outcome" not in record


def test_ps_shows_outcomes_and_error_detail(home, project):
    write_scrill(
        project / ".scrills",
        "moody",
        "moody",
        """
        EXITS = {1: "no answer"}
        def main():
            return 1
        """,
    )
    assert scrills(["run", "moody"], home, project).returncode == 1
    assert scrills(["py"], home, project, stdin="boom").returncode == 1
    legacy = {
        "verb": "run",
        "name": "fromolderversion",
        "status": "free form",
        "exit": 1,
        "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with (Path(home) / ".runs" / "log.jsonl").open("a") as handle:
        handle.write(json.dumps(legacy) + "\n")
    result = scrills(["ps"], home, project)
    assert result.returncode == 0
    summary = next(line for line in result.stdout.splitlines() if line.startswith("last 24h:"))
    assert re.search(r"\d+ outcome \([^)]*\d+ no answer[^)]*\)", summary), summary
    assert "1 free form" in summary
    counted = re.search(r"last 24h: (\d+) runs", summary)
    assert counted is not None, summary
    total = int(counted.group(1))
    buckets = re.sub(r"\([^)]*\)", "", summary.split(" - ", 1)[1])
    assert sum(int(count) for count in re.findall(r"(\d+) ", buckets)) == total, summary
    assert "- NameError at <py>:1" in result.stdout


def test_finish_waits_for_nondaemon_threads(home, project):
    write_scrill(
        project / ".scrills",
        "threader",
        "threader",
        """
        import threading, time
        def main():
            threading.Thread(target=lambda: time.sleep(1.2)).start()
            return 0
        """,
    )
    result = scrills(["run", "threader"], home, project)
    assert result.returncode == 0
    record = log_records(home, "threader")[-1]
    assert record["status"] == "ok"
    assert record["ms"] >= 1200
    assert not cards(home, "threader")


def test_log_rotation_waits_for_the_lock(home, project):
    import fcntl

    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    log = runs / "log.jsonl"
    prev = runs / "log.prev.jsonl"
    prev.write_text('{"marker": true}\n')
    filler = json.dumps({"name": "filler", "status": "ok"}) + "\n"
    log.write_text(filler * (1048576 // len(filler) + 2))
    big = log.stat().st_size
    with open(log) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = scrills(["py"], home, project, stdin="1")
        assert result.returncode == 0
        assert '{"marker": true}' in prev.read_text()
        assert log.stat().st_size > big
    result = scrills(["py"], home, project, stdin="1")
    assert result.returncode == 0
    assert prev.stat().st_size > 1048576
    assert log.stat().st_size < 4096


def test_ps_empty(tmp_path):
    fresh = tmp_path / "freshhome"
    result = scrills(["ps"], fresh, tmp_path)
    assert result.returncode == 0
    assert "running: nothing" in result.stdout
    assert "no runs recorded" in result.stdout


def test_parallel_first_contact(tmp_path):
    fresh = tmp_path / "parallelhome"
    env = {**base_env(), "SCRILLS_HOME": str(fresh)}
    children = [
        subprocess.Popen(
            [sys.executable, CLI, "py"],
            env=env,
            cwd=str(tmp_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    outcomes = [child.communicate("1", timeout=180) for child in children]
    codes = [child.returncode for child in children]
    assert codes == [0, 0, 0, 0], outcomes
    assert all(out == "1\n" for out, _ in outcomes)
    assert (fresh / ".venv" / "bin" / "python").exists()


def test_cli_under_venv_python_returns(home, project):
    venv_python = str(Path(home) / ".venv" / "bin" / "python")
    result = subprocess.run(
        [venv_python, CLI, "--version"],
        env={**base_env(), "SCRILLS_HOME": str(home)},
        cwd=str(project),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip()


def _old_python():
    path = "/usr/bin/python3"
    if not os.path.exists(path):
        return None
    probe = subprocess.run(
        [path, "-c", "import sys; print(1 if sys.version_info < (3, 10) else 0)"],
        capture_output=True,
        text=True,
    )
    return path if probe.stdout.strip() == "1" else None


@pytest.mark.skipif(_old_python() is None, reason="no pre-3.10 system python to launch with")
def test_old_launcher_relaunches_onto_venv(home, project):
    old = _old_python()
    assert old is not None
    write_scrill(
        project / ".scrills",
        "modern",
        "modern",
        """
        def main():
            match "x":
                case "x":
                    print("relaunched")
            return 0
        """,
    )
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    result = subprocess.run(
        [old, CLI, "run", "modern"],
        env=env,
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "relaunched\n"
    listing = subprocess.run(
        [old, CLI, "list"],
        env=env,
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert "- modern: modern" in listing.stdout


def test_registry_failure_is_harmless(home, project):
    write_scrill(project / ".scrills", "sturdy", "sturdy", "def main():\n    print('fine')\n    return 0")
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    mode = runs.stat().st_mode
    runs.chmod(0o000)
    try:
        result = scrills(["run", "sturdy"], home, project)
    finally:
        runs.chmod(mode)
    assert result.returncode == 0
    assert result.stdout == "fine\n"


def test_py_with_stderr_closed_still_runs(home, project):
    result = subprocess.run(
        ["/bin/sh", "-c", f'exec 2>&- ; "{sys.executable}" "{CLI}" py'],
        input="1",
        env={**base_env(), "SCRILLS_HOME": str(home)},
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "1\n"


def test_references_modules_import(home, project):
    folder = write_scrill(
        project / ".scrills",
        "withrefs",
        "---\nname: withrefs\ndescription: t\nversion: 0.0.1\n---",
        """
        from .references import helper

        def both():
            return helper.val()
        """,
    )
    (folder / "references").mkdir()
    (folder / "references" / "helper.py").write_text("def val():\n    return 42\n")
    code = (
        "from scrills import withrefs\n"
        "from scrills.withrefs.references import helper\n"
        "print(withrefs.both(), helper.val())\n"
    )
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0, result.stderr
    assert "42 42" in result.stdout


def test_a_scrill_may_carry_any_files(home, project):
    folder = write_scrill(
        project / ".scrills",
        "carry",
        "---\nname: carry\ndescription: t\nversion: 0.1.0\n---",
        """
        import json
        import os

        HERE = os.path.dirname(os.path.abspath(__file__))

        def table():
            with open(os.path.join(HERE, "data.json")) as handle:
                return json.load(handle)

        def note():
            with open(os.path.join(HERE, "assets", "deep", "notes.md")) as handle:
                return handle.read().strip()
        """,
    )
    (folder / "data.json").write_text('{"rows": 3}')
    (folder / "README.txt").write_text("not python\n")
    (folder / "assets" / "deep").mkdir(parents=True)
    (folder / "assets" / "deep" / "notes.md").write_text("# notes\n")
    listed = scrills(["list"], home, project)
    assert listed.returncode == 0
    assert "- carry: t" in listed.stdout
    assert "(doesn't parse" not in listed.stdout
    result = scrills(["py"], home, project, stdin="from scrills import carry\nprint(carry.table(), carry.note())\n")
    assert result.returncode == 0, result.stderr
    assert "{'rows': 3} # notes" in result.stdout


def test_list_raises_frontmatter_drift(home, project):
    write_scrill(project / ".scrills", "emails", "---\nname: emailz\ndescription: d\nversion: 0.1.0\n---")
    write_scrill(project / ".scrills", "bare", "---\nname: bare\n---")
    write_scrill(project / ".scrills", "clean", "---\nname: clean\ndescription: d\nversion: 0.1.0\n---")
    result = scrills(["list"], home, project)
    assert result.returncode == 0
    assert "(frontmatter name 'emailz' differs from folder 'emails' - the folder name is the import name)" in result.stdout
    assert "(no description in frontmatter)" in result.stdout
    assert "(no version in frontmatter)" in result.stdout
    clean_chunk = result.stdout.split("- clean: d")[1].split("- emails: d")[0]
    assert "(no " not in clean_chunk
    assert "differs" not in clean_chunk


def test_install_sh_installs_the_clone_it_sits_in(tmp_path):
    repo = Path(CLI).resolve().parent.parent.parent
    for label, launch in (("sh", ["sh", "install.sh", "--no-skill"]), ("dot", ["./install.sh", "--no-skill"])):
        bin_dir = tmp_path / f"bin-{label}"
        env = {
            **base_env(),
            "HOME": str(tmp_path / "fakehome"),
            "SCRILLS_BIN": str(bin_dir),
            "SCRILLS_SRC": str(tmp_path / "src"),
            "SCRILLS_REPO": str(tmp_path / "norepo"),
        }
        result = subprocess.run(launch, cwd=str(repo), env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "installing from this clone" in result.stdout
        link = bin_dir / "scrills"
        assert link.is_symlink()
        assert os.path.realpath(link) == os.path.realpath(CLI)


def test_install_sh_links_the_skill_for_claude_and_pi(tmp_path):
    repo = Path(CLI).resolve().parent.parent.parent
    fake = tmp_path / "fakehome"
    claude_skills = fake / ".claude" / "skills"
    pi_agent = fake / "custom-pi-agent"
    claude_skills.mkdir(parents=True)
    pi_agent.mkdir(parents=True)
    env = {
        **base_env(),
        "HOME": str(fake),
        "PI_CODING_AGENT_DIR": str(pi_agent),
        "SCRILLS_BIN": str(tmp_path / "bin"),
        "SCRILLS_SRC": str(tmp_path / "src"),
        "SCRILLS_REPO": str(tmp_path / "norepo"),
    }
    result = subprocess.run(["sh", "install.sh"], cwd=str(repo), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    for link in (claude_skills / "scrills", pi_agent / "skills" / "scrills"):
        assert link.is_symlink()
        assert os.path.realpath(link) == os.path.realpath(SKILL.parent)
