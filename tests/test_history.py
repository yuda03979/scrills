"""Response retention and recovery through the real CLI, with no paid or external work."""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import base_env
from test_scrills import CLI, log_records, scrills, write_scrill


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".scrills").mkdir()
    return tmp_path


def run_id(result):
    match = re.search(r"^scrills: run ([0-9a-f]{16})$", result.stderr, re.M)
    assert match, result.stderr
    return match.group(1)


def result_dir(home, ident):
    return Path(home) / ".results" / ident


def metadata(home, ident):
    return json.loads((result_dir(home, ident) / "meta.json").read_text())


def id_for_pid(home, pid):
    matches = [path.parent.name for path in (Path(home) / ".results").glob("*/meta.json")
               if json.loads(path.read_text()).get("pid") == pid]
    assert len(matches) == 1
    return matches[0]


def test_automatic_response_and_identity(home, project):
    result = scrills(["py"], home, project, stdin="import sys\nprint('answer')\nprint('diagnostic', file=sys.stderr)\n42")
    assert result.returncode == 0
    assert result.stdout == "answer\n42\n"
    ident = run_id(result)
    assert result.stderr.startswith(f"scrills: run {ident}\n")
    folder = result_dir(home, ident)
    assert (folder / "stdout").read_bytes() == b"answer\n42\n"
    assert (folder / "stderr").read_bytes() == b"diagnostic\n"
    meta = metadata(home, ident)
    assert meta["id"] == log_records(home, "py")[-1]["id"] == ident
    assert meta["complete"] is True
    assert meta["status"] == "ok" and meta["exit"] == 0
    assert meta["cwd"] == str(project)
    assert "code" not in meta and "argv" not in meta
    for path in (Path(home) / ".results", folder):
        assert path.stat().st_mode & 0o077 == 0
    for path in folder.iterdir():
        assert path.stat().st_mode & 0o077 == 0
    assert (Path(home) / ".results" / ".gitignore").read_text() == "*\n"


def test_closing_both_streams_early_still_records_completion(home, project):
    result = scrills(["py"], home, project, stdin="import os, time\nos.close(1)\nos.close(2)\ntime.sleep(.2)")
    assert result.returncode == 0
    assert metadata(home, run_id(result))["complete"] is True


def test_single_byte_binary_page_always_advances(home, project):
    result = scrills(["py"], home, project, stdin="import os\nos.write(1, b'\\x00')\npass")
    ident = run_id(result)
    shown = scrills(["history", ident, "--limit", "1"], home, project)
    assert shown.returncode == 0
    assert "\\x00" in shown.stdout
    assert "--offset 0" not in shown.stdout


def test_repr_output_precedes_the_capped_value(home, project):
    code = "class Value:\n    def __repr__(self):\n        print('repr side effect')\n        return 'x' * 20000\nValue()"
    result = scrills(["py"], home, project, stdin=code, extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("repr side effect\n")
    assert (result_dir(home, run_id(result)) / "stdout").read_text() == "repr side effect\n" + "x" * 20000 + "\n"


def test_full_echo_is_retained_before_display_cap(home, project):
    result = scrills(["py"], home, project, stdin="print('prefix')\n'x' * 20000")
    assert result.returncode == 0
    assert len(result.stdout) < 9000 and "… (+" in result.stdout
    saved = (result_dir(home, run_id(result)) / "stdout").read_text()
    assert saved == "prefix\n'" + "x" * 20000 + "'\n"
    assert "… (+" not in saved


def test_capture_includes_descriptors_children_and_binary(home, project):
    code = "import os, subprocess, sys\nos.write(1, b'\\x00\\xffFD\\n')\nsubprocess.run([sys.executable, '-I', '-c', \"import os; os.write(1,b'child\\\\n'); os.write(2,b'child-error\\\\n')\"], check=True)\npass"
    result = subprocess.run([sys.executable, CLI, "py"], input=code.encode(), cwd=project,
                            env={**base_env(), "SCRILLS_HOME": str(home)}, capture_output=True)
    assert result.returncode == 0, result.stderr
    ident = re.search(rb"scrills: run ([0-9a-f]{16})", result.stderr).group(1).decode()
    folder = result_dir(home, ident)
    assert result.stdout == (folder / "stdout").read_bytes() == b"\x00\xffFD\nchild\n"
    assert (folder / "stderr").read_bytes() == b"child-error\n"
    shown = scrills(["history", ident], home, project)
    assert shown.returncode == 0
    assert "\\x00" in shown.stdout and "\\xff" in shown.stdout
    assert "\x00" not in shown.stdout


def test_history_lists_and_reads_without_execution_or_registry(home, project):
    result = scrills(["py"], home, project, stdin="print('kept')")
    ident = run_id(result)
    log = Path(home) / ".runs" / "log.jsonl"
    before = log.read_bytes()
    listing = scrills(["history"], home, project)
    assert listing.returncode == 0 and ident in listing.stdout
    reading = scrills(["history", ident], home, project)
    assert reading.returncode == 0 and "kept" in reading.stdout
    assert "scrills: run " not in reading.stderr
    assert log.read_bytes() == before
    assert "stdout" in reading.stdout and "stderr" in reading.stdout
    assert str(result_dir(home, ident)) not in reading.stdout


def test_history_orders_runs_with_subsecond_timestamps(home, project):
    first = run_id(scrills(["py"], home, project, stdin="pass"))
    second = run_id(scrills(["py"], home, project, stdin="pass"))
    for ident in (first, second):
        assert re.search(r"T\d\d:\d\d:\d\d\.\d{6}", metadata(home, ident)["started"])
    listing = scrills(["history"], home, project)
    assert listing.stdout.index(second) < listing.stdout.index(first)


def test_history_order_uses_instants_not_timezone_spelling(tmp_path):
    home = tmp_path / "home"
    for ident, started in (("a" * 16, "2026-01-01T10:00:00+03:00"), ("b" * 16, "2026-01-01T09:00:00+00:00")):
        folder = result_dir(home, ident)
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(json.dumps({"id": ident, "started": started}))
    listing = scrills(["history"], home, tmp_path)
    assert listing.returncode == 0
    assert listing.stdout.index("b" * 16) < listing.stdout.index("a" * 16)


def test_history_does_not_need_runtime_or_original_scrill(tmp_path):
    home = tmp_path / "home"
    ident = "a" * 16
    folder = result_dir(home, ident)
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"id": ident, "name": "gone", "verb": "run", "complete": True, "status": "ok", "exit": 0, "started": "2026-01-01T00:00:00+00:00", "cwd": "/gone"}))
    (folder / "stdout").write_bytes(b"old answer\n")
    (folder / "stderr").write_bytes(b"")
    # Even an executable but hostile venv must not be relaunched by history.
    python = home / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho WRONG-RUNTIME >&2\nexit 99\n")
    python.chmod(0o755)
    result = scrills(["history", ident], home, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "old answer" in result.stdout
    assert "WRONG-RUNTIME" not in result.stderr
    assert not (home / ".runs").exists()


def test_byte_and_line_pagination(home, project):
    result = scrills(["py"], home, project, stdin="import sys\nsys.stdout.write('alpha\\nbeta\\ngamma\\n')\nsys.stderr.write('problem\\n')\npass")
    ident = run_id(result)
    page = scrills(["history", ident, "--stream", "stdout", "--offset", "6", "--limit", "5"], home, project)
    assert page.returncode == 0
    assert "beta\n" in page.stdout and "alpha\n" not in page.stdout and "gamma\n" not in page.stdout
    assert "--offset 11" in page.stdout
    line = scrills(["history", ident, "--stream", "stdout", "--line", "2", "--lines", "1"], home, project)
    assert line.returncode == 0
    assert "beta\n" in line.stdout and "gamma\n" not in line.stdout
    err = scrills(["history", ident, "--stream", "stderr"], home, project)
    assert err.returncode == 0 and "problem" in err.stdout and "alpha\n" not in err.stdout
    combined = scrills(["history", ident, "--offset", "17", "--limit", "8"], home, project)
    assert combined.returncode == 0 and "problem" in combined.stdout


def test_huge_line_remains_bounded_and_has_a_byte_cursor(home, project):
    result = scrills(["py"], home, project, stdin="import sys\nsys.stdout.write('z' * 100000 + '\\nlast\\n')\npass")
    ident = run_id(result)
    page = scrills(["history", ident, "--line", "1", "--lines", "1", "--limit", "123"], home, project)
    assert page.returncode == 0
    assert "z" * 123 in page.stdout and "z" * 124 not in page.stdout
    assert "--offset 123" in page.stdout
    assert len(page.stdout) < 2000
    last = scrills(["history", ident, "--line", "2", "--lines", "1"], home, project)
    assert last.returncode == 0 and "last\n" in last.stdout


@pytest.mark.parametrize("args", [
    ["../escape"], ["f" * 16], ["--bogus"], ["a" * 16, "--offset", "-1"],
    ["a" * 16, "--limit", "0"], ["a" * 16, "--limit", "65537"],
    ["a" * 16, "--line", "0"], ["a" * 16, "--lines", "0"],
    ["a" * 16, "--offset", "0", "--line", "1"],
    ["a" * 16, "--stream", "wat"], ["a" * 16, "extra"],
])
def test_history_usage_never_creates_a_runtime(tmp_path, args):
    home = tmp_path / "none"
    result = scrills(["history", *args], home, tmp_path)
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr
    assert not home.exists()


def test_empty_history_is_read_only(tmp_path):
    home = tmp_path / "none"
    result = scrills(["history"], home, tmp_path)
    assert result.returncode == 0 and "no responses" in result.stdout
    assert not home.exists()


def test_failed_run_response_is_retrievable(home, project):
    result = scrills(["py"], home, project, stdin="print('before')\nraise ValueError('detail')")
    assert result.returncode == 1
    ident = run_id(result)
    meta = metadata(home, ident)
    assert meta["complete"] is True and meta["status"] == "error"
    assert "ValueError: detail" in (result_dir(home, ident) / "stderr").read_text()
    response = scrills(["history", ident], home, project)
    assert response.returncode == 0
    assert "before" in response.stdout and "ValueError: detail" in response.stdout


def test_exit_handlers_and_threads_are_in_the_response(home, project):
    source = "import atexit, threading, time\natexit.register(lambda: print('at-exit'))\nthreading.Thread(target=lambda: (time.sleep(.15), print('thread'))).start()\nprint('body')"
    result = scrills(["py"], home, project, stdin=source)
    assert result.returncode == 0
    assert result.stdout == "body\nthread\nat-exit\n"
    assert (result_dir(home, run_id(result)) / "stdout").read_text() == result.stdout


def test_history_survives_registry_append_failure(home, project):
    log = Path(home) / ".runs" / "log.jsonl"
    aside = log.with_name("history-log-aside")
    log.rename(aside)
    log.mkdir()
    try:
        result = scrills(["py"], home, project, stdin="print('independent')")
    finally:
        log.rmdir()
        aside.rename(log)
    assert result.returncode == 0
    ident = run_id(result)
    assert metadata(home, ident)["complete"] is True
    assert "independent" in scrills(["history", ident], home, project).stdout


def test_history_survives_registry_registration_failure(home, project):
    runs = Path(home) / ".runs"
    mode = runs.stat().st_mode
    runs.chmod(0)
    try:
        result = scrills(["py"], home, project, stdin="print('no-card')")
    finally:
        runs.chmod(mode)
    assert result.returncode == 0, result.stderr
    ident = run_id(result)
    assert metadata(home, ident)["complete"] is True
    assert (result_dir(home, ident) / "stdout").read_text() == "no-card\n"


def test_storage_setup_failure_precedes_user_code(home, project):
    # Use a separate directory obstruction so the shared home's other results survive.
    root = Path(home) / ".results"
    aside = root.with_name(".results-aside")
    if root.exists():
        root.rename(aside)
    root.write_text("obstruction")
    sentinel = project / "must-not-run"
    try:
        result = scrills(["py"], home, project, stdin=f"from pathlib import Path\nPath({str(sentinel)!r}).touch()")
    finally:
        root.unlink()
        if aside.exists():
            aside.rename(root)
    assert result.returncode == 1
    assert "history" in result.stderr.lower()
    assert not sentinel.exists()


@pytest.mark.parametrize("shape", ["symlink", "fifo", "directory", "missing"])
def test_history_rejects_non_regular_streams(home, project, shape):
    result = scrills(["py"], home, project, stdin="print('answer')")
    ident = run_id(result)
    path = result_dir(home, ident) / "stdout"
    path.unlink()
    if shape == "symlink":
        outside = project / "private"
        outside.write_text("DO-NOT-FOLLOW")
        path.symlink_to(outside)
    elif shape == "fifo":
        os.mkfifo(path)
    elif shape == "directory":
        path.mkdir()
    reading = scrills(["history", ident], home, project)
    assert reading.returncode == 1
    assert "Traceback" not in reading.stderr
    assert "DO-NOT-FOLLOW" not in reading.stdout


def test_sigkill_has_only_incomplete_output(home, project):
    ready = project / "ready"
    code = f"import pathlib, time\nprint('started', flush=True)\npathlib.Path({str(ready)!r}).touch()\ntime.sleep(60)"
    child = subprocess.Popen([sys.executable, CLI, "py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=project, env={**base_env(), "SCRILLS_HOME": str(home)}, text=True)
    child.stdin.write(code)
    child.stdin.close()
    child.stdin = None
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        child.kill()
        out, err = child.communicate(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert child.returncode == -signal.SIGKILL
    ident = id_for_pid(home, child.pid)
    assert out == "" and err == "", "SIGKILL has no finalization response; discover its ID through history"
    assert metadata(home, ident)["complete"] is False
    shown = scrills(["history", ident], home, project)
    assert shown.returncode == 0 and "incomplete" in shown.stdout
    assert "started" in shown.stdout
    scrills(["ps"], home, project)
    assert not (Path(home) / ".runs" / f"{child.pid}.json").exists()


def test_run_outcomes_and_arguments_stay_native(home, project):
    write_scrill(project / ".scrills", "historical", "Historical fixture.", "import sys\nEXITS = {7: 'declined'}\ndef main():\n    print(sys.stdin.read().strip(), sys.argv[1:])\n    return 7")
    result = scrills(["run", "historical", "--save", "--offset", "8"], home, project, stdin="input")
    assert result.returncode == 7
    assert result.stdout == "input ['--save', '--offset', '8']\n"
    meta = metadata(home, run_id(result))
    assert meta["status"] == "outcome" and meta["outcome"] == "declined"
    assert meta["complete"] is True


def test_empty_response_and_log_rotation(home, project):
    result = scrills(["py"], home, project, stdin="pass")
    ident = run_id(result)
    folder = result_dir(home, ident)
    assert (folder / "stdout").read_bytes() == (folder / "stderr").read_bytes() == b""
    log = Path(home) / ".runs" / "log.jsonl"
    aside = log.with_name("history-rotation-aside")
    log.rename(aside)
    try:
        shown = scrills(["history", ident], home, project)
        assert shown.returncode == 0
        assert "complete" in shown.stdout and "0 bytes" in shown.stdout
    finally:
        aside.rename(log)


def test_concurrent_runs_have_distinct_responses(home, project):
    children = [subprocess.Popen([sys.executable, CLI, "py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=project,
                                env={**base_env(), "SCRILLS_HOME": str(home)}) for _ in range(8)]
    for number, child in enumerate(children):
        child.stdin.write(f"print('value-{number}')")
        child.stdin.close()
        child.stdin = None
    ids = set()
    for number, child in enumerate(children):
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        ident = re.search(r"scrills: run ([0-9a-f]{16})", err).group(1)
        ids.add(ident)
        assert (result_dir(home, ident) / "stdout").read_text() == out == f"value-{number}\n"
        assert metadata(home, ident)["complete"] is True
    assert len(ids) == 8


def test_only_final_output_is_returned(home, project):
    gate, ready = project / "gate", project / "ready"
    source = f"import pathlib, time\nprint('first', flush=True)\npathlib.Path({str(ready)!r}).touch()\nwhile not pathlib.Path({str(gate)!r}).exists():\n    time.sleep(.02)\nprint('done')"
    child = subprocess.Popen([sys.executable, CLI, "py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, cwd=project,
                             env={**base_env(), "SCRILLS_HOME": str(home)})
    child.stdin.write(source)
    child.stdin.close()
    child.stdin = None
    try:
        import select
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        assert not select.select([child.stdout, child.stderr], [], [], .2)[0]
        assert child.poll() is None
        gate.touch()
        out, err = child.communicate(timeout=15)
        assert out == "first\ndone\n" and child.returncode == 0, err
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=15)
        scrills(["ps"], home, project)


def test_binary_output_is_returned_only_after_completion(home, project):
    gate, ready = project / "gate", project / "ready"
    source = f"import os, pathlib, time\nos.write(1, b'\\x00')\npathlib.Path({str(ready)!r}).touch()\nwhile not pathlib.Path({str(gate)!r}).exists():\n    time.sleep(.02)\npass"
    child = subprocess.Popen([sys.executable, CLI, "py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=project,
                             env={**base_env(), "SCRILLS_HOME": str(home)})
    child.stdin.write(source.encode())
    child.stdin.close()
    child.stdin = None
    try:
        import select
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        assert not select.select([child.stdout], [], [], .2)[0]
        gate.touch()
        out, _ = child.communicate(timeout=15)
        assert child.returncode == 0 and out == b"\x00"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=15)
        scrills(["ps"], home, project)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_group_signal_keeps_native_exit_and_no_response_hang(home, project, sig):
    ready = project / "ready"
    source = f"import pathlib, time\nprint('ready', flush=True)\npathlib.Path({str(ready)!r}).touch()\ntime.sleep(60)"
    child = subprocess.Popen([sys.executable, CLI, "py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, cwd=project, start_new_session=True,
                             env={**base_env(), "SCRILLS_HOME": str(home)})
    child.stdin.write(source)
    child.stdin.close()
    child.stdin = None
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists()
        os.killpg(child.pid, sig)
        out, err = child.communicate(timeout=15)
        assert child.returncode == -sig
        ident = id_for_pid(home, child.pid)
        assert metadata(home, ident)["complete"] is False
        if sig == signal.SIGINT:
            assert err.count("KeyboardInterrupt") == 1
            assert "KeyboardInterrupt" in (result_dir(home, ident) / "stderr").read_text()
            assert out == "ready\n"
        else:
            assert out == "" and err == ""
        assert (result_dir(home, ident) / "stdout").read_text() == "ready\n"
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=15)
        scrills(["ps"], home, project)


def test_lost_delivery_does_not_destroy_the_response(home, project):
    source = "import sys\nsys.stdout.write('retained-' * 20000)\npass"
    child = subprocess.Popen([sys.executable, CLI, "py"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=project, text=True,
                             env={**base_env(), "SCRILLS_HOME": str(home)})
    child.stdout.close()
    child.stdout = None
    _, err = child.communicate(source, timeout=20)
    assert child.returncode == 0, err
    ident = re.search(r"scrills: run ([0-9a-f]{16})", err).group(1)
    assert (result_dir(home, ident) / "stdout").read_text() == "retained-" * 20000
    assert metadata(home, ident)["complete"] is True
    assert metadata(home, ident)["delivery_failed"] == ["stdout"]


def test_echo_substitution_preserves_unicode_and_exit_output(home, project):
    result = scrills(["py"], home, project, stdin="import atexit\natexit.register(lambda: print('after'))\nprint('before')\n'שלום' * 6000",
                     extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert result.returncode == 0
    ident = run_id(result)
    folder = result_dir(home, ident)
    assert (folder / "stdout").read_text() == "before\n'" + "שלום" * 6000 + "'\nafter\n"
    assert "\x00" not in result.stdout and "scrills-echo:" not in result.stdout
    assert len(result.stdout) < 300
    assert sorted(path.name for path in folder.iterdir()) == ["meta.json", "stderr", "stdout"]


def test_corrupt_history_is_reported_without_hiding_other_entries(home, project):
    good = scrills(["py"], home, project, stdin="1")
    good_id = run_id(good)
    bad = "e" * 16
    folder = result_dir(home, bad)
    folder.mkdir()
    (folder / "meta.json").write_text("[]")
    shown = scrills(["history", bad], home, project)
    assert shown.returncode == 1 and "Traceback" not in shown.stderr
    listing = scrills(["history"], home, project)
    assert listing.returncode == 0 and good_id in listing.stdout
    assert bad in listing.stderr


def test_root_only_layout_is_guidance_not_a_gate(home, project):
    folder = write_scrill(project / ".scrills", "organized", "Organized source.", "from .lib.api import value\ndef main():\n    print(value())\n")
    (folder / "lib").mkdir()
    (folder / "lib" / "api.py").write_text('"""A namespace subfolder needs no empty __init__.py."""\ndef value():\n    return 42\n')
    (folder / "old_helper.py").write_text('"""Existing flat files remain valid."""\n')
    result = scrills(["run", "organized"], home, project)
    assert result.returncode == 0 and result.stdout == "42\n"


@pytest.mark.parametrize("missing", ["_history.py"])
def test_missing_history_helper_fails_before_registration(home, project, missing):
    import shutil
    scripts = project / "broken-install"
    scripts.mkdir()
    for source in Path(CLI).parent.iterdir():
        if source.is_file() and source.name != missing:
            shutil.copy2(source, scripts / source.name)
    before = (Path(home) / ".runs" / "log.jsonl").read_bytes()
    result = subprocess.run([sys.executable, str(scripts / "scrills"), "py"], input="print('must not execute')",
                            env={**base_env(), "SCRILLS_HOME": str(home)}, cwd=project,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and missing in result.stderr
    assert "must not execute" not in result.stdout
    assert (Path(home) / ".runs" / "log.jsonl").read_bytes() == before


def test_nested_runs_keep_separate_ids_and_response_files(home, project):
    source = "import os, subprocess\nsubprocess.run([os.environ['SCRILLS_CLI'], 'py'], input=\"print('inner')\", text=True, check=True)\nprint('outer')"
    result = scrills(["py"], home, project, stdin=source)
    assert result.returncode == 0, result.stderr
    ids = re.findall(r"^scrills: run ([0-9a-f]{16})$", result.stderr, re.M)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert (result_dir(home, ids[0]) / "stdout").read_text() == "inner\nouter\n"
    assert (result_dir(home, ids[1]) / "stdout").read_text() == "inner\n"
    assert metadata(home, ids[1])["parent"] == ids[0]
    assert all(metadata(home, ident)["complete"] is True for ident in ids)


def test_native_stdio_buffer_is_flushed_before_response_return(home, project):
    source = "import ctypes\nlibc = ctypes.CDLL(None)\nlibc.printf(b'late C stdout\\n')\npass"
    result = scrills(["py"], home, project, stdin=source)
    assert result.returncode == 0
    assert result.stdout == "late C stdout\n"
    assert (result_dir(home, run_id(result)) / "stdout").read_bytes() == b"late C stdout\n"


def test_echo_respects_deliberate_stdout_redirection(home, project):
    target = project / "own-output"
    code = f"import os\nfd = os.open({str(target)!r}, os.O_WRONLY | os.O_CREAT, 0o600)\nos.dup2(fd, 1)\nos.close(fd)\n'x' * 20000"
    result = scrills(["py"], home, project, stdin=code, extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "recover with scrills history" in target.read_text()
    assert (result_dir(home, run_id(result)) / "stdout").read_bytes() == b""
