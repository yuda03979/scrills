"""Saved output and ID-based retrieval through the real CLI; no external or paid work."""
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


def id_for_pid(home, pid):
    return json.loads((Path(home) / ".runs" / f"{pid}.json").read_text())["id"]


def test_automatic_output_only_storage(home, project):
    result = scrills(["py"], home, project, stdin="import sys\nprint('answer')\nprint('diagnostic', file=sys.stderr)\n42")
    assert result.returncode == 0 and result.stdout == "answer\n42\n"
    ident = run_id(result)
    assert ident == log_records(home, "py")[-1]["id"]
    assert result.stderr == f"scrills: run {ident}\ndiagnostic\n"
    folder = result_dir(home, ident)
    assert sorted(path.name for path in folder.iterdir()) == ["stderr", "stdout"]
    assert (folder / "stdout").read_bytes() == b"answer\n42\n"
    assert (folder / "stderr").read_bytes() == b"diagnostic\n"
    for path in (Path(home) / ".results", folder, *folder.iterdir()):
        assert path.stat().st_mode & 0o077 == 0
    assert (Path(home) / ".results" / ".gitignore").read_text() == "*\n"


def test_closing_both_streams_early_still_returns(home, project):
    result = scrills(["py"], home, project, stdin="import os, time\nos.close(1)\nos.close(2)\ntime.sleep(.2)")
    assert result.returncode == 0
    assert (result_dir(home, run_id(result)) / "stdout").read_bytes() == b""


def test_repr_output_precedes_the_capped_value(home, project):
    code = "class Value:\n    def __repr__(self):\n        print('repr side effect')\n        return 'x' * 20000\nValue()"
    result = scrills(["py"], home, project, stdin=code, extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("repr side effect\n")
    assert (result_dir(home, run_id(result)) / "stdout").read_text() == result.stdout


@pytest.mark.parametrize("cap", ["50", "0"])
def test_saved_echo_is_exactly_the_delivered_echo(home, project, cap):
    result = scrills(["py"], home, project, stdin="print('prefix')\n'x' * 20000",
                     extra_env={"SCRILLS_ECHO_CAP": cap})
    assert result.returncode == 0
    saved = (result_dir(home, run_id(result)) / "stdout").read_text()
    assert saved == result.stdout
    assert ("… (+" in saved) == (cap != "0")
    assert "recover with scrills history" not in saved


def test_capture_and_retrieval_preserve_raw_bytes_and_children(home, project):
    code = "import os, subprocess, sys\nos.write(1, b'\\x00\\xffFD\\n')\nsubprocess.run([sys.executable, '-I', '-c', \"import os; os.write(1,b'child\\\\n'); os.write(2,b'child-error\\\\n')\"], check=True)\npass"
    env = {**base_env(), "SCRILLS_HOME": str(home)}
    result = subprocess.run([sys.executable, CLI, "py"], input=code.encode(), cwd=project, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    ident = re.search(rb"scrills: run ([0-9a-f]{16})", result.stderr).group(1).decode()
    folder = result_dir(home, ident)
    assert result.stdout == (folder / "stdout").read_bytes() == b"\x00\xffFD\nchild\n"
    assert (folder / "stderr").read_bytes() == b"child-error\n"
    for offset, expected in ((0, b"\x00"), (1, b"\xff")):
        page = subprocess.run([sys.executable, CLI, "history", ident, "--offset", str(offset), "--limit", "1"],
                              env=env, cwd=project, capture_output=True, timeout=10)
        assert page.returncode == 0 and page.stdout == expected
        assert f"--offset {offset + 1}".encode() in page.stderr
        assert str(folder).encode() not in page.stdout + page.stderr


def test_history_lists_ids_and_reads_without_registry_writes(home, project):
    result = scrills(["py"], home, project, stdin="print('kept')")
    ident = run_id(result)
    log = Path(home) / ".runs" / "log.jsonl"
    before = log.read_bytes()
    listing = scrills(["history"], home, project)
    assert listing.returncode == 0 and ident in listing.stdout.splitlines()
    assert all(re.fullmatch(r"[0-9a-f]{16}", line) for line in listing.stdout.splitlines())
    reading = scrills(["history", ident], home, project)
    assert reading.returncode == 0 and reading.stdout == "kept\n"
    assert str(result_dir(home, ident)) not in reading.stdout + reading.stderr
    assert log.read_bytes() == before


def test_history_byte_pages_are_independent_per_stream(home, project):
    result = scrills(["py"], home, project, stdin="import sys\nprint('alpha\\nbeta\\ngamma')\nprint('problem', file=sys.stderr)")
    ident = run_id(result)
    page = scrills(["history", ident, "--offset", "6", "--limit", "5"], home, project)
    assert page.returncode == 0 and page.stdout == "beta\n"
    assert f"history {ident} --stream stdout --offset 11 --limit 5" in page.stderr
    err = scrills(["history", ident, "--stream", "stderr", "--offset", "1", "--limit", "3"], home, project)
    assert err.returncode == 0 and err.stdout == "rob"
    assert "--stream stderr --offset 4 --limit 3" in err.stderr
    end = scrills(["history", ident, "--offset", "11"], home, project)
    assert end.returncode == 0 and end.stdout == "gamma\n" and "next:" not in end.stderr
    beyond = scrills(["history", ident, "--offset", "100"], home, project)
    assert beyond.returncode == 0 and beyond.stdout == ""


def test_history_default_read_is_bounded(home, project):
    result = scrills(["py"], home, project, stdin="print('x' * 100000)")
    ident = run_id(result)
    page = scrills(["history", ident], home, project)
    assert page.returncode == 0 and page.stdout == "x" * 8192
    assert "--offset 8192" in page.stderr
    larger = scrills(["history", ident, "--limit", "65536"], home, project)
    assert larger.returncode == 0 and larger.stdout == "x" * 65536


def test_history_ignores_old_metadata_and_needs_no_runtime(tmp_path):
    home = tmp_path / "home"
    ident = "a" * 16
    folder = result_dir(home, ident)
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text("old metadata is not even valid JSON")
    (folder / "stdout").write_bytes(b"old answer\n")
    (folder / "stderr").write_bytes(b"")
    python = home / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho WRONG-RUNTIME >&2\nexit 99\n")
    python.chmod(0o755)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    result = scrills(["history", ident], home, tmp_path)
    assert result.returncode == 0 and result.stdout == "old answer\n", result.stderr
    assert "WRONG-RUNTIME" not in result.stderr
    assert scrills(["history"], home, tmp_path).stdout == ident + "\n"
    assert not (home / ".runs").exists()
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}


@pytest.mark.parametrize("args", [["--line", "1"], ["--lines", "1"], ["--stream", "both"]])
def test_history_has_no_line_or_combined_stream_modes(home, project, args):
    ident = run_id(scrills(["py"], home, project, stdin="print('saved')"))
    result = scrills(["history", ident, *args], home, project)
    assert result.returncode == 2 and "usage:" in result.stderr


@pytest.mark.parametrize("args", [
    ["../escape"], ["f" * 16], ["--bogus"], ["a" * 16, "extra"], ["--offset", "0"],
    ["a" * 16, "--offset", "-1"], ["a" * 16, "--limit", "0"], ["a" * 16, "--limit", "65537"],
])
def test_history_usage_never_creates_a_runtime(tmp_path, args):
    home = tmp_path / "none"
    result = scrills(["history", *args], home, tmp_path)
    assert result.returncode == 2, result.stderr
    assert "Traceback" not in result.stderr and not home.exists()


def test_empty_history_is_read_only(tmp_path):
    home = tmp_path / "none"
    result = scrills(["history"], home, tmp_path)
    assert result.returncode == 0 and "no responses" in result.stdout
    assert not home.exists()


def test_failed_run_output_is_retrievable(home, project):
    result = scrills(["py"], home, project, stdin="print('before')\nraise ValueError('detail')")
    assert result.returncode == 1
    ident = run_id(result)
    response = scrills(["history", ident, "--stream", "stderr"], home, project)
    assert response.returncode == 0
    assert response.stdout == (result_dir(home, ident) / "stderr").read_text()
    assert "ValueError: detail" in response.stdout
    assert scrills(["history", ident], home, project).stdout == "before\n"


def test_exit_handlers_and_threads_are_in_the_response(home, project):
    source = "import atexit, threading, time\natexit.register(lambda: print('at-exit'))\nthreading.Thread(target=lambda: (time.sleep(.15), print('thread'))).start()\nprint('body')"
    result = scrills(["py"], home, project, stdin=source)
    assert result.returncode == 0 and result.stdout == "body\nthread\nat-exit\n"
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
    assert scrills(["history", run_id(result)], home, project).stdout == "independent\n"


def test_history_survives_registry_registration_failure(home, project):
    runs = Path(home) / ".runs"
    mode = runs.stat().st_mode
    runs.chmod(0)
    try:
        result = scrills(["py"], home, project, stdin="print('no-card')")
    finally:
        runs.chmod(mode)
    assert result.returncode == 0, result.stderr
    assert (result_dir(home, run_id(result)) / "stdout").read_text() == "no-card\n"


@pytest.mark.parametrize("verb", ["py", "run"])
def test_storage_setup_failure_warns_and_runs_normally(home, project, verb):
    root = Path(home) / ".results"
    aside = root.with_name(".results-aside")
    root.rename(aside)
    root.write_text("obstruction")
    sentinel = project / "executed"
    source = f"import sys\nfrom pathlib import Path\nPath({str(sentinel)!r}).touch()\nprint('ran once')\nprint('diagnostic', file=sys.stderr)\nsys.exit(7)"
    write_scrill(project / ".scrills", "fallback", "Fallback fixture.", f"def main():\n    exec({source!r})")
    try:
        result = scrills([verb, *(["fallback"] if verb == "run" else [])], home, project, stdin=source)
    finally:
        root.unlink()
        aside.rename(root)
    assert result.returncode == 7, result.stderr
    assert result.stdout == "ran once\n" and sentinel.exists()
    assert "running without saved output" in result.stderr
    assert "diagnostic" in result.stderr and "scrills: run " not in result.stderr
    assert log_records(home, "py" if verb == "py" else "fallback")[-1]["exit"] == 7


def test_partial_redirection_failure_restores_caller_streams(home, project):
    source = f"""
import importlib.machinery, importlib.util, os, sys
loader = importlib.machinery.SourceFileLoader('cli', {CLI!r})
core = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(core)
original = os.dup2
calls = 0
def fail_second(*args):
    global calls
    calls += 1
    if calls == 2:
        raise OSError('injected stderr redirection failure')
    return original(*args)
os.dup2 = fail_second
sys.argv = [{CLI!r}, 'py']
core.main()
"""
    before = set((Path(home) / ".results").iterdir())
    result = subprocess.run([str(Path(home) / ".venv/bin/python"), "-I", "-c", source],
                            input="import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(7)",
                            env={**base_env(), "SCRILLS_HOME": str(home)}, cwd=project,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 7, result.stderr
    assert result.stdout == "out\n" and result.stderr.endswith("err\n")
    assert "running without saved output" in result.stderr and "scrills: run " not in result.stderr
    assert set((Path(home) / ".results").iterdir()) == before


@pytest.mark.parametrize("shape", ["symlink", "fifo", "directory", "missing"])
def test_history_rejects_non_regular_streams(home, project, shape):
    ident = run_id(scrills(["py"], home, project, stdin="print('answer')"))
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
    assert "Traceback" not in reading.stderr and str(path) not in reading.stderr
    assert "DO-NOT-FOLLOW" not in reading.stdout


def test_sigkill_leaves_available_bytes_without_a_completion_claim(home, project):
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
    assert child.returncode == -signal.SIGKILL and out == err == ""
    ident = id_for_pid(home, child.pid)
    assert ident in scrills(["history"], home, project).stdout.splitlines()
    shown = scrills(["history", ident], home, project)
    assert shown.returncode == 0 and shown.stdout == "started\n"
    scrills(["ps"], home, project)
    assert not (Path(home) / ".runs" / f"{child.pid}.json").exists()


def test_run_outcomes_and_arguments_stay_native(home, project):
    write_scrill(project / ".scrills", "historical", "Historical fixture.", "import sys\nEXITS = {7: 'declined'}\ndef main():\n    print(sys.stdin.read().strip(), sys.argv[1:])\n    return 7")
    result = scrills(["run", "historical", "--save", "--offset", "8"], home, project, stdin="input")
    assert result.returncode == 7 and result.stdout == "input ['--save', '--offset', '8']\n"
    assert log_records(home, "historical")[-1]["outcome"] == "declined"


def test_empty_response_does_not_depend_on_registry(home, project):
    ident = run_id(scrills(["py"], home, project, stdin="pass"))
    log = Path(home) / ".runs" / "log.jsonl"
    aside = log.with_name("history-rotation-aside")
    log.rename(aside)
    try:
        shown = scrills(["history", ident], home, project)
        assert shown.returncode == 0 and shown.stdout == ""
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
    assert len(ids) == 8


@pytest.mark.parametrize("write,expected", [("print('first', flush=True)", b"first\ndone\n"),
                                           ("os.write(1, b'\\x00')", b"\x00done\n")])
def test_only_final_output_is_returned(home, project, write, expected):
    gate, ready = project / "gate", project / "ready"
    source = f"import os, pathlib, time\n{write}\npathlib.Path({str(ready)!r}).touch()\nwhile not pathlib.Path({str(gate)!r}).exists():\n    time.sleep(.02)\nprint('done')"
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
        assert not select.select([child.stdout, child.stderr], [], [], .2)[0]
        assert child.poll() is None
        gate.touch()
        out, err = child.communicate(timeout=15)
        assert out == expected and child.returncode == 0, err
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


def test_saved_echo_matches_unicode_and_exit_output(home, project):
    result = scrills(["py"], home, project, stdin="import atexit\natexit.register(lambda: print('after'))\nprint('before')\n'שלום' * 6000",
                     extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert result.returncode == 0
    folder = result_dir(home, run_id(result))
    assert (folder / "stdout").read_text() == result.stdout
    assert result.stdout.startswith("before\n'") and result.stdout.endswith("after\n")
    assert len(result.stdout) < 300
    assert sorted(path.name for path in folder.iterdir()) == ["stderr", "stdout"]


def test_root_only_layout_is_guidance_not_a_gate(home, project):
    folder = write_scrill(project / ".scrills", "organized", "Organized source.", "from .lib.api import value\ndef main():\n    print(value())\n")
    (folder / "lib").mkdir()
    (folder / "lib" / "api.py").write_text('"""A namespace subfolder needs no empty __init__.py."""\ndef value():\n    return 42\n')
    (folder / "old_helper.py").write_text('"""Existing flat files remain valid."""\n')
    result = scrills(["run", "organized"], home, project)
    assert result.returncode == 0 and result.stdout == "42\n"


def test_missing_history_helper_fails_before_registration(home, project):
    import shutil
    scripts = project / "broken-install"
    scripts.mkdir()
    for source in Path(CLI).parent.iterdir():
        if source.is_file() and source.name != "_runtime.py":
            shutil.copy2(source, scripts / source.name)
    before = (Path(home) / ".runs" / "log.jsonl").read_bytes()
    result = subprocess.run([sys.executable, str(scripts / "scrills"), "py"], input="print('must not execute')",
                            env={**base_env(), "SCRILLS_HOME": str(home)}, cwd=project,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and "_runtime.py" in result.stderr
    assert "must not execute" not in result.stdout
    assert (Path(home) / ".runs" / "log.jsonl").read_bytes() == before


def test_nested_runs_keep_separate_ids_and_ordered_output(home, project):
    source = "import os, subprocess\nsubprocess.run([os.environ['SCRILLS_CLI'], 'py'], input=\"print('inner')\", text=True, check=True)\nprint('outer')"
    result = scrills(["py"], home, project, stdin=source)
    assert result.returncode == 0 and result.stdout == "inner\nouter\n", result.stderr
    ids = re.findall(r"^scrills: run ([0-9a-f]{16})$", result.stderr, re.M)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert (result_dir(home, ids[0]) / "stdout").read_text() == result.stdout
    assert (result_dir(home, ids[1]) / "stdout").read_text() == "inner\n"
    assert next(record for record in log_records(home, "py") if record.get("id") == ids[1])["parent"] == ids[0]


def test_native_stdio_buffer_is_flushed_before_response_return(home, project):
    source = "import ctypes\nlibc = ctypes.CDLL(None)\nlibc.printf(b'late C stdout\\n')\npass"
    result = scrills(["py"], home, project, stdin=source)
    assert result.returncode == 0 and result.stdout == "late C stdout\n"
    assert (result_dir(home, run_id(result)) / "stdout").read_bytes() == b"late C stdout\n"


def test_history_last_twenty_uses_file_times_and_never_deletes(tmp_path):
    home = tmp_path / "home"
    for number in range(21):
        folder = result_dir(home, f"{number:016x}")
        folder.mkdir(parents=True)
        (folder / "stdout").write_text("older response\n")
        (folder / "stderr").touch()
        os.utime(folder, (number + 1, number + 1))
    listing = scrills(["history"], home, tmp_path)
    assert listing.returncode == 0
    assert listing.stdout.splitlines() == [f"{number:016x}" for number in range(20, 0, -1)]
    oldest = scrills(["history", "0" * 16], home, tmp_path)
    assert oldest.returncode == 0 and oldest.stdout == "older response\n"
    assert len(list((home / ".results").iterdir())) == 21


def test_echo_respects_deliberate_stdout_redirection(home, project):
    target = project / "own-output"
    code = f"import os\nfd = os.open({str(target)!r}, os.O_WRONLY | os.O_CREAT, 0o600)\nos.dup2(fd, 1)\nos.close(fd)\n'x' * 20000"
    result = scrills(["py"], home, project, stdin=code, extra_env={"SCRILLS_ECHO_CAP": "50"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "print it or write it to a file" in target.read_text()
    assert (result_dir(home, run_id(result)) / "stdout").read_bytes() == b""
