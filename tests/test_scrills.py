# Drives the real CLI as a subprocess: a session-scoped user home (one venv build), a fresh
# project directory per test. Run: uv run --with pytest==8.4.2 python -m pytest tests/ -q
import ast
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
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


def load_cli_module():
    loader = importlib.machinery.SourceFileLoader("scrills_cli_test", CLI)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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


def write_skill_md(root, name, text):
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(text)
    return folder


def entry_chunk(stdout, entry_line):
    tail = stdout.split(entry_line + "\n", 1)[1]
    kept = []
    for line in tail.splitlines():
        if line.startswith("- "):
            break
        kept.append(line)
    return "\n".join(kept)


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


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match syntax needs Python 3.10+")
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


def test_lost_project_layer_is_said(home, tmp_path):
    caller = tmp_path / "callerproj" / ".scrills"
    caller.mkdir(parents=True)
    nowhere = tmp_path / "state"
    nowhere.mkdir()
    inherited = {"SCRILLS_LAYERS": f"{caller}{os.pathsep}{home}"}
    result = scrills(["py"], home, nowhere, stdin="1", extra_env=inherited)
    assert result.returncode == 0
    assert "the caller had one" in result.stderr
    assert str(caller) in result.stderr
    where = scrills(["where"], home, nowhere, extra_env=inherited)
    assert "the caller had one" in where.stdout
    assert str(caller) in where.stdout


def test_lost_project_stays_silent(home, project, tmp_path):
    caller = tmp_path / "elsewhere" / ".scrills"
    caller.mkdir(parents=True)
    inherited = {"SCRILLS_LAYERS": f"{caller}{os.pathsep}{home}"}
    resolved = scrills(["py"], home, project, stdin="1", extra_env=inherited)
    assert resolved.returncode == 0
    assert "caller had" not in resolved.stderr
    homeonly = scrills(["py"], home, tmp_path, stdin="1", extra_env={"SCRILLS_LAYERS": str(home)})
    assert homeonly.returncode == 0
    assert "caller had" not in homeonly.stderr
    plain = scrills(["py"], home, tmp_path, stdin="1")
    assert plain.returncode == 0
    assert "caller had" not in plain.stderr


def test_lost_project_notice_travels_the_real_chain(home, project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("statehome")
    write_scrill(
        project / ".scrills",
        "spawner",
        "spawner",
        f"""
        import os
        import subprocess
        import sys
        def main():
            child = subprocess.run(
                [os.environ["SCRILLS_CLI"], "py"],
                input="1",
                cwd={str(outside)!r},
                capture_output=True,
                text=True,
            )
            sys.stderr.write(child.stderr)
            return child.returncode
        """,
    )
    result = scrills(["run", "spawner"], home, project)
    assert result.returncode == 0, result.stderr
    assert "the caller had one" in result.stderr
    assert str(project / ".scrills") in result.stderr


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


def test_run_rejects_project_stray_that_shadows_user_scrill(home, project):
    write_scrill(home, "straywinner", "user package", "def main():\n    print('user ran')\n    return 0")
    (project / ".scrills" / "straywinner.py").write_text("print('project stray ran')\ndef main():\n    return 0\n")
    result = scrills(["run", "straywinner"], home, project)
    assert result.returncode == 2
    assert "stray module" in result.stderr
    assert "project stray ran" not in result.stdout
    assert "user ran" not in result.stdout
    assert not log_records(home, "straywinner")
    assert not cards(home, "straywinner")
    unknown = scrills(["run", "no_such_winner"], home, project)
    refusal = next(line for line in unknown.stderr.splitlines() if "there's no scrill named" in line)
    assert "straywinner" not in refusal


def test_run_no_main(home, project):
    write_scrill(project / ".scrills", "libonly", "libonly", "print('imported')\nVALUE = 1")
    result = scrills(["run", "libonly"], home, project)
    assert result.returncode == 2
    assert "defines no main()" in result.stderr
    # runnability is gated once, by the boot after the import - so the top level runs first
    # (convention holds it to imports/constants/defs, and a scrill may bind main at import)
    assert "imported" in result.stdout


def test_boot_ctx_is_popped_before_user_code(home, project):
    result = scrills(["py"], home, project, stdin="import os\n'SCRILLS_BOOT_CTX' in os.environ")
    assert result.returncode == 0
    assert result.stdout == "False\n"


def test_run_argv_carries_no_boot_source(home, project):
    write_scrill(project / ".scrills", "sleeperargv", "sleeperargv", "import time\ndef main():\n    time.sleep(30)\n    return 0")
    child = subprocess.Popen(
        [sys.executable, CLI, "run", "sleeperargv"],
        env={**base_env(), "SCRILLS_HOME": str(home)},
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
        argv = subprocess.run(["ps", "-ww", "-p", str(child.pid), "-o", "command="], capture_output=True, text=True).stdout
        assert "_boot.py" in argv
        assert "import atexit" not in argv  # the boot source no longer travels in argv
        assert "CARD =" not in argv  # and neither does the interpolated run card
    finally:
        child.kill()
        child.wait()


def test_missing_boot_file_fails_cleanly(home, project, tmp_path):
    fake_scripts = tmp_path / "scripts"
    fake_scripts.mkdir()
    for name in ("scrills", "_scrills_pth.py"):
        (fake_scripts / name).write_text((Path(CLI).parent / name).read_text())
    result = subprocess.run(
        [sys.executable, str(fake_scripts / "scrills"), "py"],
        input="1",
        env={**base_env(), "SCRILLS_HOME": str(home)},
        cwd=str(project),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "_boot.py is missing" in result.stderr
    assert "can't open file" not in result.stderr
    assert not cards(home, "py")  # the refusal happens before a run is registered


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


@pytest.mark.parametrize("method", ["spawn", "forkserver", "default"])
def test_run_process_pool_over_scrill(home, project, method):
    write_scrill(project / ".scrills", "poolworker", "pool worker", "def double(n):\n    return n * 2")
    write_scrill(
        project / ".scrills",
        "poolrunner",
        "pool runner",
        """
        import multiprocessing
        import sys
        from concurrent.futures import ProcessPoolExecutor
        from scrills import poolworker

        def main():
            options = {} if sys.argv[1] == "default" else {"mp_context": multiprocessing.get_context(sys.argv[1])}
            with ProcessPoolExecutor(max_workers=2, **options) as pool:
                print(list(pool.map(poolworker.double, [1, 2, 3])))
            return 0
        """,
    )
    result = scrills(["run", "poolrunner", method], home, project)
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


def test_invalid_venv_lock_has_the_writable_library_remedy(tmp_path):
    fresh = tmp_path / "lockedhome"
    lock = fresh / ".runs" / "venv.lock"
    lock.mkdir(parents=True)
    result = scrills(["py"], fresh, tmp_path, stdin="1")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert str(lock) in result.stderr
    assert f"The user library ({fresh}) must be a writable directory" in result.stderr


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


def test_concurrent_ps_records_each_death_once(home, project):
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    ids = set()
    for i in range(400):
        pid = 9000000 + i
        ident = f"{i:016x}"
        ids.add(ident)
        card = {
            "verb": "run",
            "name": f"ghost{i}",
            "id": ident,
            "pid": pid,
            "cwd": str(project),
            "who": "test",
            "started": "2026-10-09T00:00:00+00:00",
        }
        (runs / f"{pid}.json").write_text(json.dumps(card))
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    children = [
        subprocess.Popen(
            [sys.executable, CLI, "ps"],
            env=env,
            cwd=str(project),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(16)
    ]
    for child in children:
        assert child.wait() == 0
    records = [json.loads(line) for line in (runs / "log.jsonl").read_text().splitlines()]
    died = [record for record in records if record.get("id") in ids and record.get("status") == "died"]
    assert len(died) == 400
    assert len({record["id"] for record in died}) == 400
    assert not [path for path in runs.glob("*.json") if path.stem.isdigit() and 9000000 <= int(path.stem) < 9000400]


def test_ps_does_not_rerecord_a_finished_run(home, project):
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    ident = "cafecafecafecafe"
    started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with (runs / "log.jsonl").open("a") as handle:
        handle.write(
            json.dumps({"verb": "run", "name": "alreadydone", "id": ident, "pid": 9876540, "status": "ok", "exit": 0, "started": started}) + "\n"
        )
    card = {"verb": "run", "name": "alreadydone", "id": ident, "pid": 9876540, "cwd": str(project), "who": "test", "started": started}
    (runs / "9876540.json").write_text(json.dumps(card))
    result = scrills(["ps"], home, project)
    assert result.returncode == 0
    assert [record["status"] for record in log_records(home, "alreadydone")] == ["ok"]
    assert not (runs / "9876540.json").exists()


def test_ps_keeps_the_card_when_the_append_fails(home, project):
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    log = runs / "log.jsonl"
    aside = runs / "log.jsonl.aside"
    if log.exists():
        os.rename(log, aside)
    log.mkdir()
    card = {
        "verb": "run",
        "name": "unrecordable",
        "id": "deadbeefdeadbeef",
        "pid": 9876541,
        "cwd": str(project),
        "who": "test",
        "started": "2026-10-09T00:00:00+00:00",
    }
    card_path = runs / "9876541.json"
    card_path.write_text(json.dumps(card))
    try:
        result = scrills(["ps"], home, project)
        assert result.returncode == 0
        assert card_path.exists()
        assert "could not record" in result.stdout
        assert "found now, recorded" not in result.stdout
    finally:
        log.rmdir()
        if aside.exists():
            os.rename(aside, log)
    result = scrills(["ps"], home, project)
    assert result.returncode == 0
    assert not card_path.exists()
    assert [record["status"] for record in log_records(home, "unrecordable")] == ["died"]


def test_ps_keeps_and_names_an_unreadable_card(home, project):
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    bad = runs / "9876542.json"
    bad.write_text("{not json")
    try:
        result = scrills(["ps"], home, project)
        assert result.returncode == 0
        assert bad.exists()
        assert "9876542.json" in result.stderr
        records = [json.loads(line) for line in (runs / "log.jsonl").read_text().splitlines()]
        assert not [record for record in records if record.get("pid") == 9876542]
    finally:
        bad.unlink(missing_ok=True)


def test_py_records_unknown_who_when_not_a_tty(home, project):
    result = scrills(["py"], home, project, stdin="1")
    assert result.returncode == 0
    assert log_records(home, "py")[-1]["who"] == "unknown"


def test_py_records_tty_who_on_a_terminal(home, project):
    import pty

    master, slave = pty.openpty()
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    child = subprocess.Popen(
        [sys.executable, CLI, "py"],
        env=env,
        cwd=str(project),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=slave,
        text=True,
    )
    os.close(slave)
    child.stdin.write("1")
    child.stdin.close()
    child.stdin = None
    assert child.wait(timeout=30) == 0
    os.close(master)
    assert log_records(home, "py")[-1]["who"] == "tty"


def test_py_sigint_dies_naturally(home, project):
    ready = project / "sigint-ready"
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    child = subprocess.Popen(
        [sys.executable, CLI, "py"],
        env=env,
        cwd=str(project),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    child.stdin.write(f"import pathlib, time\npathlib.Path({str(ready)!r}).write_text('1')\ntime.sleep(30)\n")
    child.stdin.close()
    child.stdin = None
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready.exists()
    card = Path(home) / ".runs" / f"{child.pid}.json"
    assert card.exists()
    before = len(log_records(home, "py"))
    os.kill(child.pid, signal.SIGINT)
    assert child.wait(timeout=10) == -signal.SIGINT
    assert child.stderr.read().count("KeyboardInterrupt") == 1
    assert len(log_records(home, "py")) == before
    assert card.exists()
    scrills(["ps"], home, project)
    record = log_records(home, "py")[-1]
    assert record["status"] == "died"
    assert record["pid"] == child.pid
    assert not card.exists()


def test_run_sigint_dies_naturally(home, project):
    ready = project / "sigint-ready"
    write_scrill(
        project / ".scrills",
        "sigintable",
        "sigintable",
        f"import pathlib, time\ndef main():\n    pathlib.Path({str(ready)!r}).write_text('1')\n    time.sleep(30)\n    return 0",
    )
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    child = subprocess.Popen(
        [sys.executable, CLI, "run", "sigintable"],
        env=env,
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    card = Path(home) / ".runs" / f"{child.pid}.json"
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready.exists()
    assert card.exists()
    os.kill(child.pid, signal.SIGINT)
    assert child.wait(timeout=10) == -signal.SIGINT
    assert child.stderr.read().count("KeyboardInterrupt") == 1
    assert card.exists()
    scrills(["ps"], home, project)
    record = log_records(home, "sigintable")[-1]
    assert record["status"] == "died"
    assert not card.exists()


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
    lock = runs / "log.lock"
    lock.touch()
    with open(lock) as held:
        fcntl.flock(held, fcntl.LOCK_SH)
        result = scrills(["py"], home, project, stdin="1")
        assert result.returncode == 0
        assert '{"marker": true}' in prev.read_text()
        assert log.stat().st_size > big
    result = scrills(["py"], home, project, stdin="1")
    assert result.returncode == 0
    assert prev.stat().st_size > 1048576
    assert log.stat().st_size < 4096


def test_log_reader_cannot_lose_a_file_to_rotation(tmp_path):
    core = load_cli_module()
    home = tmp_path / "loghome"
    runs = home / ".runs"
    runs.mkdir(parents=True)
    prev = runs / "log.prev.jsonl"
    log = runs / "log.jsonl"
    prev_record = {"id": "prev", "name": "prev"}
    current_record = {"id": "current", "name": "current"}
    arriving_record = {"id": "arriving", "name": "arriving"}
    prev.write_text(json.dumps(prev_record) + "\n")
    log.write_text(json.dumps(current_record) + "\n" + "x" * 1048576 + "\n")
    original_read = core.read_text
    rotated = False

    def read_with_arrival(path):
        nonlocal rotated
        text = original_read(path)
        if os.path.basename(path) == "log.prev.jsonl" and not rotated:
            rotated = True
            assert core.append_log(str(home), arriving_record)
        return text

    core.read_text = read_with_arrival
    records = core.log_records(str(home))
    assert {record.get("id") for record in records} >= {"prev", "current", "arriving"}
    assert json.loads(prev.read_text().splitlines()[0]) == prev_record


def test_sweep_snapshots_ids_after_all_liveness_probes(tmp_path):
    core = load_cli_module()
    home = tmp_path / "sweephome"
    runs = home / ".runs"
    runs.mkdir(parents=True)
    first = {"id": "first-dead", "name": "first", "pid": 9100001, "started": "2026-10-09T00:00:00+00:00"}
    finishing = {"id": "finishing", "name": "finishing", "pid": 9100002, "started": "2026-10-09T00:00:00+00:00"}
    duplicate = {**first, "name": "duplicate", "pid": 9100003}
    for card in (first, finishing, duplicate):
        (runs / f"{card['pid']}.json").write_text(json.dumps(card))
    finalized = False

    def finalize_while_probing(path, pid):
        nonlocal finalized
        if pid == finishing["pid"] and not finalized:
            finalized = True
            assert core.append_log(str(home), {**finishing, "status": "ok", "exit": 0})
        return False

    core.held = finalize_while_probing
    _, died, unrecorded = core.sweep(str(home))
    records = core.log_records(str(home))
    assert not unrecorded
    assert [card["id"] for card in died] == [first["id"]]
    assert [record["status"] for record in records if record.get("id") == first["id"]] == ["died"]
    assert [record["status"] for record in records if record.get("id") == finishing["id"]] == ["ok"]
    assert not list(runs.glob("*.json"))


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
    venv = str(Path(home) / ".venv" / "bin" / "python")

    def major_minor(python):
        result = subprocess.run(
            [python, "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"],
            capture_output=True,
            text=True,
        )
        return tuple(int(part) for part in result.stdout.strip().split("."))

    if major_minor(venv) <= major_minor(old):
        pytest.skip("the venv interpreter is not newer than the launcher")
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


def test_skill_md_manifest_lists_imports_and_runs(home, project):
    folder = write_skill_md(
        project / ".scrills",
        "filed",
        "---\nname: filed\ndescription: manual in a file\nversion: 1.2.3\n---\n\nprose body\n",
    )
    (folder / "__init__.py").write_text("def main():\n    print('ran')\n    return 0\n")
    listing = scrills(["list"], home, project)
    assert "- filed: manual in a file" in listing.stdout
    chunk = entry_chunk(listing.stdout, "- filed: manual in a file")
    assert "announces nothing" not in chunk
    assert "(no " not in chunk
    assert "prose body" not in listing.stdout
    ran = scrills(["run", "filed"], home, project)
    assert ran.returncode == 0
    assert ran.stdout == "ran\n"
    assert log_records(home, "filed")[-1]["imported"] == {"filed": "1.2.3"}


def test_skill_md_is_the_manual_and_a_manifest_docstring_is_ignored_aloud(home, project):
    folder = write_scrill(
        project / ".scrills",
        "twice",
        "---\nname: twice\ndescription: from the docstring\nversion: 0.1.0\n---",
        "VALUE = 1",
    )
    (folder / "SKILL.md").write_text("---\nname: twice\ndescription: from the file\nversion: 0.2.0\n---\n")
    listing = scrills(["list"], home, project)
    assert "- twice: from the file" in listing.stdout
    assert "from the docstring" not in listing.stdout
    assert "ignored: SKILL.md is the manual, the docstring explains the file" in listing.stdout
    used = scrills(["py"], home, project, stdin="from scrills import twice\ntwice.VALUE")
    assert used.returncode == 0
    assert used.stdout == "1\n"
    assert log_records(home, "py")[-1]["imported"] == {"twice": "0.2.0"}


def test_a_prose_skill_md_is_still_the_manual(home, project):
    folder = write_scrill(
        project / ".scrills",
        "mixed",
        "---\nname: mixed\ndescription: from the docstring\nversion: 0.1.0\n---",
        "VALUE = 3",
    )
    (folder / "SKILL.md").write_text("readme prose only, no fences\nsecond line\n")
    listing = scrills(["list"], home, project)
    assert "- mixed: readme prose only, no fences" in listing.stdout
    assert "from the docstring" not in listing.stdout
    chunk = entry_chunk(listing.stdout, "- mixed: readme prose only, no fences")
    assert "(no frontmatter - open SKILL.md with" in chunk
    assert "ignored: SKILL.md is the manual, the docstring explains the file" in chunk
    used = scrills(["py"], home, project, stdin="from scrills import mixed\nmixed.VALUE")
    assert used.returncode == 0
    assert log_records(home, "py")[-1]["imported"] == {"mixed": None}


def test_skill_md_beside_a_prose_docstring_is_silent(home, project):
    folder = write_scrill(project / ".scrills", "paired", "helper notes for readers", "VALUE = 2")
    (folder / "SKILL.md").write_text("---\nname: paired\ndescription: d\nversion: 0.1.0\n---\n")
    listing = scrills(["list"], home, project)
    assert "- paired: d" in listing.stdout
    assert "helper notes" not in listing.stdout
    chunk = entry_chunk(listing.stdout, "- paired: d")
    assert "SKILL.md wins" not in chunk
    assert "(no " not in chunk


def test_skill_md_manifest_survives_a_broken_init(home, project):
    folder = write_skill_md(
        project / ".scrills",
        "brokefile",
        "---\nname: brokefile\ndescription: still described\nversion: 0.1.0\n---\n",
    )
    (folder / "__init__.py").write_text("def main(:\n    pass\n")
    listing = scrills(["list"], home, project)
    assert "- brokefile: still described" in listing.stdout
    assert "doesn't parse" in listing.stdout


def test_skill_md_notices_unclosed_prose_and_name_drift(home, project):
    unclosed = write_skill_md(project / ".scrills", "unfiled", "---\nname: unfiled\ndescription: d")
    (unclosed / "__init__.py").write_text("VALUE = 1\n")
    prose = write_skill_md(project / ".scrills", "prosefile", "a readme-ish first line\nsecond-prose-line\n")
    (prose / "__init__.py").write_text("VALUE = 2\n")
    drifted = write_skill_md(project / ".scrills", "renamed", "---\nname: rename\ndescription: d\nversion: 0.1.0\n---\n")
    (drifted / "__init__.py").write_text("VALUE = 3\n")
    listing = scrills(["list"], home, project)
    assert "- unfiled:\n" in listing.stdout
    assert "never closes" in listing.stdout
    assert "- prosefile: a readme-ish first line" in listing.stdout
    assert "second-prose-line" not in listing.stdout
    assert "(no frontmatter" in listing.stdout
    assert "announces nothing" not in listing.stdout
    assert "(frontmatter name 'rename' differs from folder 'renamed' - the folder name is the import name)" in listing.stdout


def test_nested_metadata_version_is_accepted_in_both_homes(home, project):
    write_scrill(
        project / ".scrills",
        "nested",
        '---\nname: nested\ndescription: d\nmetadata:\n  version: "3.1.4"\n---',
        "VALUE = 1",
    )
    filed = write_skill_md(
        project / ".scrills",
        "nestedfile",
        '---\nname: nestedfile\ndescription: d\nmetadata:\n  version: "2.7.1"\n---\n',
    )
    (filed / "__init__.py").write_text("VALUE = 2\n")
    listing = scrills(["list"], home, project)
    assert "(no version" not in listing.stdout
    used = scrills(["py"], home, project, stdin="from scrills import nested, nestedfile\nnested.VALUE + nestedfile.VALUE")
    assert used.returncode == 0
    assert used.stdout == "3\n"
    assert log_records(home, "py")[-1]["imported"] == {"nested": "3.1.4", "nestedfile": "2.7.1"}


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


@pytest.mark.parametrize(
    "missing",
    ["scrills/scripts/_boot.py", "scrills/scripts/_scrills_pth.py", "scrills/SKILL.md"],
)
def test_install_sh_rejects_an_incomplete_clone(tmp_path, missing):
    repo = Path(CLI).resolve().parent.parent.parent
    clone = tmp_path / missing.replace("/", "-")
    for relative in ("install.sh", "scrills/scripts/scrills", "scrills/scripts/_boot.py", "scrills/scripts/_scrills_pth.py", "scrills/SKILL.md"):
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, target)
    (clone / missing).unlink()
    bin_dir = tmp_path / ("bin-" + Path(missing).name)
    result = subprocess.run(
        ["sh", "install.sh", "--no-skill"],
        cwd=str(clone),
        env={**base_env(), "HOME": str(tmp_path / "fakehome"), "SCRILLS_BIN": str(bin_dir)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert missing in result.stderr
    assert not (bin_dir / "scrills").exists()


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


def test_ps_survives_wrong_shape_records(home, project):
    runs = Path(home) / ".runs"
    runs.mkdir(exist_ok=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    card = {"verb": "run", "name": "nullghost", "id": "deadbeefdeadbeef", "pid": dead.pid, "cwd": None, "who": "test", "started": started}
    (runs / f"{dead.pid}.json").write_text(json.dumps(card))
    with (runs / "log.jsonl").open("a") as handle:
        handle.write(json.dumps({"verb": "run", "name": "nullcwd", "pid": 1, "cwd": None, "exit": 1, "status": "error", "error_type": "ValueError", "started": started}) + "\n")
    result = scrills(["ps"], home, project)
    assert result.returncode == 0, result.stderr
    assert "nullghost" in result.stdout
    assert "nullcwd" in result.stdout
    assert not (runs / f"{dead.pid}.json").exists()
    again = scrills(["ps"], home, project)
    assert again.returncode == 0, again.stderr


def test_run_traceback_carries_no_plumbing(home, project):
    write_scrill(project / ".scrills", "raiser", "raiser", "def main():\n    raise ValueError('x')")
    result = scrills(["run", "raiser"], home, project)
    assert result.returncode == 1
    assert "ValueError" in result.stderr
    assert "in main" in result.stderr
    assert "<string>" not in result.stderr
    assert "<frozen" not in result.stderr
    assert "importlib" not in result.stderr
    write_scrill(
        project / ".scrills",
        "araiser",
        "araiser",
        "import asyncio\nasync def main():\n    await asyncio.sleep(0)\n    raise ValueError('x')",
    )
    result = scrills(["run", "araiser"], home, project)
    assert result.returncode == 1
    assert "ValueError" in result.stderr
    assert "in main" in result.stderr
    assert "<string>" not in result.stderr
    assert "asyncio" not in result.stderr
    write_scrill(project / ".scrills", "badparse", "badparse", "def main(:\n    pass")
    result = scrills(["run", "badparse"], home, project)
    assert result.returncode == 1
    assert "SyntaxError" in result.stderr
    assert "__init__.py" in result.stderr
    assert "<string>" not in result.stderr
    assert "<frozen" not in result.stderr
    assert "importlib" not in result.stderr


def test_missing_scrill_error_names_the_library(home, project):
    write_scrill(project / ".scrills", "realone", "realone", "VALUE = 1")
    result = scrills(["py"], home, project, stdin="from scrills import nope")
    assert result.returncode == 1
    assert "ImportError" in result.stderr
    assert "no scrill named nope" in result.stderr
    assert "realone" in result.stderr
    dotted = scrills(["py"], home, project, stdin="import scrills.nope")
    assert dotted.returncode == 1
    assert "no scrill named nope" in dotted.stderr
    submodule = scrills(["py"], home, project, stdin="import scrills.realone.nope")
    assert submodule.returncode == 1
    assert "no scrill named" not in submodule.stderr


def test_missing_package_error_points_at_install(home, project):
    result = scrills(["py"], home, project, stdin="import definitely_missing_pkg_xyz")
    assert result.returncode == 1
    assert "ModuleNotFoundError" in result.stderr
    assert "scrills install definitely_missing_pkg_xyz" in result.stderr


def test_run_scrill_missing_dependency_gets_the_hint(home, project):
    write_scrill(project / ".scrills", "needy", "needy", "from scrills import nope\ndef main():\n    return 0")
    result = scrills(["run", "needy"], home, project)
    assert result.returncode == 1
    assert "no scrill named nope" in result.stderr


def test_unreadable_ignored_folder_is_reported_by_list_and_run(home, project):
    folder = project / ".scrills" / "sealed"
    folder.mkdir()
    (folder / "main.py").write_text("def main():\n    return 0\n")
    mode = folder.stat().st_mode
    folder.chmod(0)
    try:
        try:
            os.listdir(folder)
        except PermissionError:
            pass
        else:
            pytest.skip("this user can still enumerate a mode-000 folder")
        listing = scrills(["list"], home, project)
        assert listing.returncode == 0
        assert "Traceback" not in listing.stderr
        assert "sealed" in listing.stdout
        assert "cannot inspect" in listing.stdout
        result = scrills(["run", "sealed"], home, project)
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert "cannot inspect" in result.stderr
    finally:
        folder.chmod(mode)


def test_list_raises_folders_that_never_resolve(home, project):
    layer = project / ".scrills"
    (layer / "deep-research").mkdir()
    (layer / "deep-research" / "__init__.py").write_text('\"\"\"x\"\"\"\n')
    (layer / "skillonly").mkdir()
    (layer / "skillonly" / "SKILL.md").write_text("---\nname: skillonly\ndescription: d\nversion: 1.0\n---\n")
    (layer / "entryless").mkdir()
    (layer / "entryless" / "main.py").write_text("def main():\n    return 0\n")
    (layer / "dangling").mkdir()
    (layer / "dangling" / "__init__.py").symlink_to(layer / "dangling" / "gone.py")
    listing = scrills(["list"], home, project)
    assert listing.returncode == 0
    assert "- deep-research:" not in listing.stdout
    assert "deep-research" in listing.stdout
    assert "not a valid Python identifier" in listing.stdout
    assert "skillonly" in listing.stdout
    assert "skill folder" in listing.stdout
    assert "entryless" in listing.stdout
    assert "no __init__.py" in listing.stdout
    assert "dangling" in listing.stdout
    assert "dangling symlink" in listing.stdout


def test_run_refusal_explains_ignored_folders_and_strays(home, project):
    layer = project / ".scrills"
    (layer / "deep-research").mkdir()
    (layer / "deep-research" / "__init__.py").write_text('\"\"\"x\"\"\"\n')
    (layer / "skillonly").mkdir()
    (layer / "skillonly" / "SKILL.md").write_text("---\nname: skillonly\ndescription: d\nversion: 1.0\n---\n")
    (layer / "straymod.py").write_text("VALUE = 1\n")
    result = scrills(["run", "deep-research"], home, project)
    assert result.returncode == 2
    assert "not a valid Python identifier" in result.stderr
    result = scrills(["run", "skillonly"], home, project)
    assert result.returncode == 2
    assert "skill folder" in result.stderr
    result = scrills(["run", "straymod"], home, project)
    assert result.returncode == 2
    assert "stray module" in result.stderr
    assert "__init__.py" in result.stderr


def test_stray_messages_when_a_folder_wins(home, project):
    write_scrill(project / ".scrills", "loose", "loose folder", "KIND = 'package'")
    (project / ".scrills" / "loose.py").write_text("KIND = 'stray'\n")
    listing = scrills(["list"], home, project)
    assert "loose.py" in listing.stdout
    assert "the loose/ folder beside it wins" in listing.stdout
    assert "importable as scrills.loose" not in listing.stdout
    write_scrill(project / ".scrills", "wins", "project folder", "KIND = 'project'")
    (Path(home) / "wins.py").write_text("KIND = 'user stray'\n")
    listing = scrills(["list"], home, project)
    assert "wins.py" in listing.stdout
    assert "shadowed by the project scrill wins" in listing.stdout


def test_read_verbs_reject_arguments(home, project):
    for verb in ("list", "ps", "where"):
        result = scrills([verb, "--json"], home, project)
        assert result.returncode == 2, verb
        assert "unknown argument" in result.stderr, verb
    result = scrills(["--version", "extra"], home, project)
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def test_scrills_home_is_a_file_fails_cleanly(tmp_path):
    notadir = tmp_path / "notadir"
    notadir.write_text("x")
    proj = tmp_path / "proj"
    proj.mkdir()
    result = scrills(["py"], notadir, proj, stdin="1")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert str(notadir) in result.stderr


def test_boot_and_resolver_are_real_parseable_files():
    # the boot and the resolver source live as real files beside the CLI: they must compile,
    # under the 3.9 grammar too (a stock launcher may be old, and the venv may have been built by one)
    for name in ("_boot.py", "_scrills_pth.py"):
        source = (Path(CLI).parent / name).read_text()
        ast.parse(source, filename=name)
        ast.parse(source, filename=name, feature_version=(3, 9))


def test_docs_include_offsets():
    # docs pages include repo files by line offset; the offset must skip exactly the intended
    # head block, or edits leak frontmatter/title text into the built site silently
    repo = Path(CLI).resolve().parent.parent.parent
    skill_lines = (repo / "scrills" / "SKILL.md").read_text().splitlines()
    closing = skill_lines.index("---", 1)
    assert skill_lines[0] == "---"
    expected = {
        "docs/manual.md": ("scrills/SKILL.md", closing + 1),
        "docs/index.md": ("README.md", 2),
    }
    for doc, (target, skipped) in expected.items():
        text = (repo / doc).read_text()
        match = re.search(r'--8<--\s+"' + re.escape(target) + r':(\d+)"', text)
        assert match is not None, f"{doc}: include of {target} not found"
        assert int(match.group(1)) == skipped + 1, (
            f"{doc} includes {target} starting at line {match.group(1)}, but the block it must skip "
            f"ends at line {skipped} - fix the offset so it skips exactly that block"
        )
    assert (repo / "README.md").read_text().splitlines()[0].startswith("# ")
