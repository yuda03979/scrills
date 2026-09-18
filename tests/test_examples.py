# Drives the shipped examples/.scrills layer through the real CLI. Offline: claude, say, ioreg,
# terminal-notifier and osascript are PATH stubs that log what they were given; media's cache
# is seeded so search/outline/vision run without pypdf, and the real-parse test skips unless
# the session venv carries pypdf (the live proof covers it).
# Run: uv run --with pytest==8.4.2 python -m pytest tests/ -q
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import base_env

CLI = str(Path(__file__).resolve().parent.parent / "scrills" / "scripts" / "scrills")
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

CLAUDE_STUB = """#!{python}
import json, os, re, sys, time
argv = sys.argv[1:]
task = sys.stdin.read()
page_file = re.search(r"(\\S+page-(\\d+)\\.\\d+\\.pdf)", task)
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "task": task, "cwd": os.getcwd(), "page_file_exists": bool(page_file and os.path.exists(page_file.group(1)))}) + "\\n")
if "sleep" in task:
    time.sleep(1.0)
session = "sess-new"
if "--resume" in argv:
    session = argv[argv.index("--resume") + 1]
    if "--fork-session" in argv:
        session = "fork-of-" + session
envelope = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "pong from stub",
    "session_id": session,
    "total_cost_usd": float(os.environ.get("STUB_COST", "0.01")),
    "num_turns": 1,
    "duration_ms": 5,
    "modelUsage": {"stub-model": {}},
}
if "--json-schema" in argv:
    if page_file and os.environ.get("STUB_UNREADABLE") == "1":
        envelope["structured_output"] = {"read": False, "text": "no poppler here"}
    elif page_file:
        envelope["structured_output"] = {"read": True, "text": "transcribed page " + page_file.group(2)}
    else:
        envelope["structured_output"] = json.loads(os.environ.get("STUB_STRUCTURED", '{"verdict": "ok", "summary": "stub summary", "issues": []}'))
    envelope["result"] = json.dumps(envelope["structured_output"])
if "overspend" in task:
    envelope.update(subtype="error_max_budget_usd", is_error=True)
    envelope.pop("result")
    print(json.dumps(envelope))
    sys.exit(1)
if "boom" in task:
    envelope.update(subtype="error_during_execution", is_error=True, result="it exploded")
    print(json.dumps(envelope))
    sys.exit(1)
print(json.dumps(envelope))
"""

OSASCRIPT_STUB = """#!{python}
import os, sys
script = sys.stdin.read()
with open(os.environ["STUB_DIALOG_LOG"], "a", encoding="utf-8") as handle:
    handle.write(script + "\\n----\\n")
if "display notification" in script:
    sys.exit(0)
mode = os.environ.get("STUB_DIALOG", "answer")
if mode == "cancel":
    sys.stderr.write("execution error: User canceled. (-128)\\n")
    sys.exit(1)
if mode == "gaveup":
    print("GAVE-UP")
elif "choose from list" in script:
    print("ANSWER:b")
else:
    print("ANSWER:yes please")
"""


def scrills(args, home, stdin=None, extra_env=None, cwd=EXAMPLES):
    return subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin,
        env={**base_env(), "SCRILLS_HOME": str(home), **(extra_env or {})},
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def write_stub(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)
    return path


def first_on_path(directory):
    return {"PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"}


def claude_env(tmp_path, **extra):
    bin_dir = tmp_path / "claude-bin"
    write_stub(bin_dir, "claude", CLAUDE_STUB.replace("{python}", sys.executable))
    log = tmp_path / "claude-calls.jsonl"
    return {**first_on_path(bin_dir), "STUB_LOG": str(log), **extra}, log


def calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def lines(path):
    return [line for line in path.read_text().splitlines() if line] if path.exists() else []


def wait_for(condition, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def clean_state(home):
    def clear(name):
        shutil.rmtree(Path(home) / ".state" / name, ignore_errors=True)

    return clear


def test_list_shows_the_five(home):
    result = scrills(["list"], home)
    assert result.returncode == 0
    for name in ("audit", "harness_config", "media", "nudge", "subagent"):
        assert f"{name}  (project)" in result.stdout
        assert f"runs standalone: scrills run {name}" in result.stdout
    assert "(no " not in result.stdout
    assert "differs from folder" not in result.stdout


def test_subagent_defaults_ledger_and_task_off_argv(home, tmp_path, clean_state):
    clean_state("subagent")
    env, log = claude_env(tmp_path)
    code = "from scrills import subagent\nprint(subagent.run('say pong secretly'))\nprint(subagent.spent('all'))\n"
    result = scrills(["py"], home, stdin=code, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert "pong from stub" in result.stdout
    assert "'calls': 1" in result.stdout
    (call,) = calls(log)
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--safe-mode" in argv
    assert argv[argv.index("--max-budget-usd") + 1] == "1.0"
    assert call["task"] == "say pong secretly"
    assert not any("secretly" in item for item in argv)
    ledger = (Path(home) / ".state" / "subagent" / "ledger.jsonl").read_text()
    assert "secretly" not in ledger
    assert "pong from stub" not in ledger
    entry = json.loads(ledger.splitlines()[0])
    assert entry["requested"] == "sonnet"
    assert entry["models"] == ["stub-model"]
    assert entry["ok"] is True


def test_subagent_map_and_amap_run_in_parallel_and_keep_failures_in_place(home, tmp_path):
    env, _ = claude_env(tmp_path)
    code = (
        "import time\n"
        "from scrills import subagent\n"
        "tasks = ['sleep a', 'sleep b', 'sleep c', 'boom d']\n"
        "began = time.time()\n"
        "answers = subagent.map(tasks, limit=4)\n"
        "print('sync', round(time.time() - began, 2), [type(a).__name__ for a in answers])\n"
        "began = time.time()\n"
        "answers = await subagent.amap(tasks, limit=4)\n"
        "print('async', round(time.time() - began, 2), [type(a).__name__ for a in answers])\n"
        "print(answers[3])\n"
    )
    result = scrills(["py"], home, stdin=code, extra_env=env)
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines()[:2]:
        _, seconds, kinds = line.split(" ", 2)
        assert float(seconds) < 2.5, line
        assert kinds == "['str', 'str', 'str', 'SubagentError']"
    assert "it exploded" in result.stdout


def test_subagent_map_total_budget_stops_starting_tasks(home, tmp_path):
    env, log = claude_env(tmp_path, STUB_COST="0.5")
    code = "from scrills import subagent\nprint([str(a) for a in subagent.map(['one', 'two', 'three'], limit=1, total_usd=1.0)])\n"
    result = scrills(["py"], home, stdin=code, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert len(calls(log)) == 2
    assert "not started - this map already spent $1.00 of total_usd=1.0" in result.stdout


def test_subagent_sessions_resume_across_calls_and_fork(home, tmp_path, clean_state):
    clean_state("subagent")
    env, log = claude_env(tmp_path)
    first = (
        "from scrills import subagent\n"
        "review = subagent.session('review')\n"
        "review.ask('one')\n"
        "review.ask('two')\n"
        "review.fork('branch').ask('three')\n"
    )
    result = scrills(["py"], home, stdin=first, extra_env=env)
    assert result.returncode == 0, result.stderr
    second = (
        "from scrills import subagent\n"
        "subagent.session('review').ask('four')\n"
        "for card in subagent.sessions():\n"
        "    print(card['name'], card['session_id'], card['asks'], card['cwd'])\n"
    )
    result = scrills(["py"], home, stdin=second, extra_env=env)
    assert result.returncode == 0, result.stderr
    argvs = [call["argv"] for call in calls(log)]
    assert "--resume" not in argvs[0]
    assert argvs[1][argvs[1].index("--resume") + 1] == "sess-new"
    assert "--fork-session" in argvs[2]
    assert argvs[3][argvs[3].index("--resume") + 1] == "sess-new"
    assert "--fork-session" not in argvs[3]
    assert f"review sess-new 3 {EXAMPLES.resolve()}" in result.stdout
    assert f"branch fork-of-sess-new 1 {EXAMPLES.resolve()}" in result.stdout


def test_subagent_schema_returns_the_object(home, tmp_path):
    env, log = claude_env(tmp_path, STUB_STRUCTURED='{"word": "pong"}')
    code = "from scrills import subagent\nanswer = subagent.run('a word please', schema={'type': 'object'})\nprint(type(answer).__name__, answer['word'])\n"
    result = scrills(["py"], home, stdin=code, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert "dict pong" in result.stdout
    assert "--json-schema" in calls(log)[0]["argv"]


def test_subagent_budget_stop_is_clear_and_still_logged(home, tmp_path, clean_state):
    clean_state("subagent")
    env, _ = claude_env(tmp_path)
    result = scrills(["run", "subagent", "--budget", "0.25", "overspend", "please"], home, extra_env=env)
    assert result.returncode == 1
    assert "stopped at the $0.25 budget" in result.stderr
    ledger = (Path(home) / ".state" / "subagent" / "ledger.jsonl").read_text()
    assert json.loads(ledger.splitlines()[-1])["ok"] is False


def test_subagent_program_flags(home, tmp_path):
    env, log = claude_env(tmp_path)
    result = scrills(["run", "subagent", "--model", "haiku", "--no-lean", "--budget", "none", "reverse", "the", "polarity"], home, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pong from stub"
    (call,) = calls(log)
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "haiku"
    assert "--safe-mode" not in argv
    assert "--max-budget-usd" not in argv
    assert call["task"] == "reverse the polarity"


def test_subagent_spent_sessions_forget_cli(home, tmp_path, clean_state):
    clean_state("subagent")
    env, _ = claude_env(tmp_path, STUB_COST="0.25")
    asked = scrills(["run", "subagent", "--session", "chat", "hello"], home, extra_env=env)
    assert asked.returncode == 0, asked.stderr
    spent = scrills(["run", "subagent", "--spent", "all"], home, extra_env=env)
    assert "$0.25 across 1 calls (all)" in spent.stdout
    listed = scrills(["run", "subagent", "--sessions"], home, extra_env=env)
    assert listed.stdout.startswith("chat  1 asks  $0.25")
    forgot = scrills(["run", "subagent", "--forget", "chat"], home, extra_env=env)
    assert "forgot chat" in forgot.stdout


def test_subagent_missing_claude(home, tmp_path):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    result = scrills(["run", "subagent", "hi"], home, extra_env={"PATH": str(empty)})
    assert result.returncode == 1
    assert "no 'claude' on PATH" in result.stderr


def seed_document(home, path, pages, outline=None):
    folder = Path(home) / ".state" / "media" / hashlib.sha256(Path(path).read_bytes()).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"pages": len(pages)}))
    for number, body in enumerate(pages, 1):
        (folder / f"page-{number}.txt").write_text(body)
    if outline is not None:
        (folder / "outline.json").write_text(json.dumps(outline))
    return folder


def fake_pdf(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 seeded " + os.urandom(16))
    return path


def test_media_search_outline_and_text_from_cache(home, tmp_path):
    book = fake_pdf(tmp_path, "book.pdf")
    seed_document(
        home,
        book,
        [
            "Chapter one: motion in a straight line, velocity and acceleration explained.",
            "Chapter two covers Newton's second\nlaw: force equals mass times acceleration. The second law again.",
            "Chapter three: energy and work, and no mention of that law here.",
        ],
        outline=[{"level": 0, "title": "Mechanics", "page": 1}, {"level": 1, "title": "Newton", "page": 2}],
    )
    code = (
        "from scrills import media\n"
        f"path = {str(book)!r}\n"
        "print(media.search(path, 'SECOND   law'))\n"
        "print(media.outline(path))\n"
        "print(media.text(path, pages='3'))\n"
    )
    result = scrills(["py"], home, stdin=code)
    assert result.returncode == 0, result.stderr
    found, marks, third = result.stdout.splitlines()[:3]
    assert "'page': 2" in found and "'hits': 2" in found and "Newton's second law" in found
    assert "'page': 1" not in found and "'page': 3" not in found
    assert "'title': 'Newton'" in marks
    assert third.startswith("Chapter three")
    cli = scrills(["run", "media", "search", str(book), "second", "law"], home)
    assert cli.stdout.startswith("p.2 (2): ")
    tree = scrills(["run", "media", "outline", str(book)], home)
    assert tree.stdout == "Mechanics  p.1\n  Newton  p.2\n"


def test_media_vision_reads_textless_pages_once(home, tmp_path):
    env, log = claude_env(tmp_path)
    scan = fake_pdf(tmp_path, "scan.pdf")
    folder = seed_document(home, scan, [""])
    code = f"from scrills import media\nprint(media.text({str(scan)!r}, vision=True))\n"
    first = scrills(["py"], home, stdin=code, extra_env=env)
    assert first.returncode == 0, first.stderr
    assert "transcribed page 1" in first.stdout
    (call,) = calls(log)
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == "Read"
    assert "--restricted" in argv
    assert "--json-schema" in argv
    assert call["cwd"] == str(folder.resolve())
    assert call["page_file_exists"] is True
    assert not list(folder.glob("*.pdf"))
    again = scrills(["py"], home, stdin=code, extra_env=env)
    assert "transcribed page 1" in again.stdout
    assert len(calls(log)) == 1
    found = scrills(["run", "media", "search", str(scan), "transcribed"], home, extra_env=env)
    assert found.stdout.startswith("p.1 (1): ")


def test_media_vision_never_caches_an_unreadable_page(home, tmp_path):
    env, log = claude_env(tmp_path, STUB_UNREADABLE="1")
    scan = fake_pdf(tmp_path, "unreadable.pdf")
    folder = seed_document(home, scan, [""])
    first = scrills(["run", "media", str(scan), "--vision"], home, extra_env=env)
    assert first.returncode == 1
    assert "nothing cached for them" in first.stderr
    assert "no poppler here" in first.stderr
    assert "--vision-usd" not in first.stderr
    assert not list(folder.glob("*.vision.txt"))
    assert not list(folder.glob("*.pdf"))
    scrills(["run", "media", str(scan), "--vision"], home, extra_env=env)
    assert len(calls(log)) == 2


def test_media_vision_budget_caps_total_spend(home, tmp_path):
    env, log = claude_env(tmp_path, STUB_COST="0.5")
    scan = fake_pdf(tmp_path, "capped.pdf")
    folder = seed_document(home, scan, [""])
    result = scrills(["run", "media", str(scan), "--vision", "--vision-usd", "0"], home, extra_env=env)
    assert result.returncode == 1
    assert "vision failed on 1 page(s)" in result.stderr
    assert "not started" in result.stderr
    assert "--vision-usd" in result.stderr
    assert not list(folder.glob("*.vision.txt"))
    assert not list(folder.glob("*.pdf"))
    assert calls(log) == []
    zero = scrills(
        ["py"],
        home,
        stdin=f"from scrills import media\nmedia.text({str(scan)!r}, vision=True, vision_usd=0)\n",
        extra_env=env,
    )
    assert zero.returncode == 1
    assert "vision_usd=0" in zero.stderr
    assert calls(log) == []
    uncapped = scrills(["run", "media", str(scan), "--vision", "--vision-usd", "none"], home, extra_env=env)
    assert uncapped.returncode == 0, uncapped.stderr
    assert "transcribed page 1" in uncapped.stdout
    assert len(calls(log)) == 1


def test_media_reversed_range_raises(home, tmp_path):
    book = fake_pdf(tmp_path, "rev.pdf")
    seed_document(home, book, ["alpha", "beta", "gamma"])
    result = scrills(["run", "media", str(book), "--pages", "3-1"], home)
    assert result.returncode == 1
    assert "reversed" in result.stderr


def test_media_cli_hints_vision_for_textless_pages(home, tmp_path):
    blank = fake_pdf(tmp_path, "blank.pdf")
    seed_document(home, blank, [""])
    result = scrills(["run", "media", str(blank)], home)
    assert result.returncode == 0
    assert "add --vision" in result.stderr


def test_media_without_pypdf_says_how_to_install(home, tmp_path):
    probe = scrills(["py"], home, stdin="import pypdf")
    if probe.returncode == 0:
        pytest.skip("session venv has pypdf; the missing-package message can't be observed")
    target = tmp_path / "empty.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    result = scrills(["run", "media", str(target)], home)
    assert result.returncode == 1
    assert "scrills install pypdf" in result.stderr


def test_media_unknown_extension(home, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("plain")
    result = scrills(["run", "media", str(target)], home)
    assert result.returncode == 1
    assert "no handler for '.txt'" in result.stderr
    assert ".pdf" in result.stderr


def test_media_page_spec(home):
    code = (
        "from scrills import media\n"
        "print(media._page_numbers(None, 3))\n"
        "print(media._page_numbers('2', 3))\n"
        "print(media._page_numbers('1-2,3', 3))\n"
        "try:\n"
        "    media._page_numbers('9', 3)\n"
        "except ValueError as error:\n"
        "    print(error)\n"
    )
    result = scrills(["py"], home, stdin=code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:3] == ["[0, 1, 2]", "[1]", "[0, 1, 2]"]
    assert "page 9 out of range" in result.stdout


def build_pdf(path, lines=("Hello scrills",)):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for line in lines:
        stream = f"BT /F1 24 Tf 72 720 Td ({line}) Tj ET".encode() if line else b""
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    Path(path).write_bytes(bytes(out))


def test_media_pdf_text_when_pypdf_present(home, tmp_path):
    probe = scrills(["py"], home, stdin="import pypdf")
    if probe.returncode != 0:
        pytest.skip("session venv has no pypdf (kept offline); the live proof covers the real parse")
    target = tmp_path / "hello.pdf"
    build_pdf(target)
    result = scrills(["run", "media", str(target)], home)
    assert result.returncode == 0, result.stderr
    assert "Hello scrills" in result.stdout
    found = scrills(["run", "media", "search", str(target), "hello"], home)
    assert found.stdout.startswith("p.1 (1): ")


def test_media_vision_budget_keeps_paid_pages_when_pypdf_present(home, tmp_path):
    probe = scrills(["py"], home, stdin="import pypdf")
    if probe.returncode != 0:
        pytest.skip("session venv has no pypdf (kept offline); the live proof covers cutting pages")
    env, log = claude_env(tmp_path, STUB_COST="0.5")
    target = tmp_path / "fivescans.pdf"
    build_pdf(target, ("", "", "", "", ""))
    folder = Path(home) / ".state" / "media" / hashlib.sha256(target.read_bytes()).hexdigest()
    result = scrills(["run", "media", str(target), "--vision", "--vision-usd", "0.4"], home, extra_env=env)
    assert result.returncode == 1
    assert "vision failed on 1 page(s)" in result.stderr
    assert "not started" in result.stderr
    assert len(list(folder.glob("*.vision.txt"))) == 4
    assert len(calls(log)) == 4


def test_media_vision_cuts_one_page_when_pypdf_present(home, tmp_path):
    probe = scrills(["py"], home, stdin="import pypdf")
    if probe.returncode != 0:
        pytest.skip("session venv has no pypdf (kept offline); the live proof covers cutting pages")
    env, log = claude_env(tmp_path)
    target = tmp_path / "mixed.pdf"
    build_pdf(target, ("A text page long enough to count as a real text layer", ""))
    result = scrills(["run", "media", str(target), "--vision"], home, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert "A text page long enough" in result.stdout
    assert "transcribed page 2" in result.stdout
    (call,) = calls(log)
    assert call["page_file_exists"] is True


def nudge_env(tmp_path, idle_seconds=600, **extra):
    bin_dir = tmp_path / "nudge-bin"
    speaks = tmp_path / "speaks.log"
    notes = tmp_path / "notes.log"
    dialogs = tmp_path / "dialogs.log"
    write_stub(bin_dir, "say", f'#!/bin/sh\ncat >> "{speaks}"\necho >> "{speaks}"\n')
    write_stub(bin_dir, "terminal-notifier", f'#!/bin/sh\ncat >> "{notes}"\necho >> "{notes}"\n')
    write_stub(bin_dir, "ioreg", "#!/bin/sh\necho '    |   \"HIDIdleTime\" = '\"$STUB_IDLE_NS\"\n")
    write_stub(bin_dir, "osascript", OSASCRIPT_STUB.replace("{python}", sys.executable))
    env = {**first_on_path(bin_dir), "STUB_IDLE_NS": str(int(idle_seconds * 1e9)), "STUB_DIALOG_LOG": str(dialogs), **extra}
    return env, speaks, notes, dialogs


def nudge_cards(home):
    state = Path(home) / ".state" / "nudge"
    return sorted(state.glob("*.json")) if state.exists() else []


@pytest.fixture
def quiet_nudges(home):
    yield
    scrills(["run", "nudge", "stop", "--all"], home)


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_voice_when_away_notification_when_present(home, tmp_path):
    env, speaks, notes, _ = nudge_env(tmp_path / "away", idle_seconds=600, SCRILLS_WHO="test:away")
    started = scrills(["run", "nudge", "question one", "0.02"], home, extra_env=env)
    assert started.returncode == 0, started.stderr
    ident = started.stdout.split()[1].rstrip(":")
    assert f"stop with: scrills run nudge stop {ident}" in started.stdout
    assert wait_for(lambda: lines(speaks).count("question one") >= 2), "nudger never re-spoke"
    assert lines(notes) == []
    shown = scrills(["run", "nudge", "status"], home, extra_env=env)
    assert f"nudge: {ident} every 0.02m" in shown.stdout
    assert "question one" in shown.stdout
    stopped = scrills(["run", "nudge", "stop", ident], home, extra_env=env)
    assert f"stopped {ident}" in stopped.stdout
    assert nudge_cards(home) == []
    here_env, here_speaks, here_notes, _ = nudge_env(tmp_path / "here", idle_seconds=2, SCRILLS_WHO="test:here")
    started = scrills(["run", "nudge", "question two", "10"], home, extra_env=here_env)
    assert started.returncode == 0, started.stderr
    assert wait_for(lambda: "question two" in lines(here_notes)), "present user got no notification"
    assert lines(here_speaks) == []


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_stop_is_scoped_by_default(home, tmp_path):
    alpha_env, *_ = nudge_env(tmp_path / "alpha", SCRILLS_WHO="test:alpha")
    beta_env, *_ = nudge_env(tmp_path / "beta", SCRILLS_WHO="test:beta")
    alpha = scrills(["run", "nudge", "from alpha", "10"], home, extra_env=alpha_env).stdout.split()[1]
    beta = scrills(["run", "nudge", "from beta", "10"], home, extra_env=beta_env).stdout.split()[1]
    stopped = scrills(["run", "nudge", "stop"], home, extra_env=alpha_env)
    assert stopped.stdout.strip() == f"nudge: stopped {alpha}"
    shown = scrills(["run", "nudge", "status"], home, extra_env=alpha_env)
    assert beta in shown.stdout
    assert "[from test:beta]" in shown.stdout
    assert alpha not in shown.stdout
    everything = scrills(["run", "nudge", "stop", "--all"], home, extra_env=alpha_env)
    assert beta in everything.stdout
    assert nudge_cards(home) == []


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_message_never_in_argv(home, tmp_path):
    env, speaks, *_ = nudge_env(tmp_path, SCRILLS_WHO="test:argv")
    ident = scrills(["run", "nudge", "private question 7431", "10"], home, extra_env=env).stdout.split()[1]
    pid = int((Path(home) / ".state" / "nudge" / f"{ident}.pid").read_text())
    args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout
    assert ident in args
    assert "7431" not in args
    assert wait_for(lambda: "private question 7431" in lines(speaks))


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_cap_ends_on_its_own(home, tmp_path):
    env, speaks, *_ = nudge_env(tmp_path, SCRILLS_WHO="test:cap")
    started = scrills(["run", "nudge", "capped", "0"], home, extra_env=env)
    assert started.returncode == 0, started.stderr
    assert wait_for(lambda: lines(speaks).count("capped") >= 12), "capped nudger never reached the cap"
    assert wait_for(lambda: nudge_cards(home) == []), "capped nudger never exited"
    time.sleep(0.3)
    assert lines(speaks).count("capped") == 12
    assert not list((Path(home) / ".state" / "nudge").glob("*.pid"))


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_stop_never_signals_a_recycled_pid(home, tmp_path):
    env, *_ = nudge_env(tmp_path, SCRILLS_WHO="test:ghost")
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        state = Path(home) / ".state" / "nudge"
        state.mkdir(parents=True, exist_ok=True)
        ident = "abc123"
        card = {"id": ident, "message": "ghost", "minutes": 5.0, "scope": "test:ghost", "started": time.time() - 3600}
        (state / f"{ident}.json").write_text(json.dumps(card))
        (state / f"{ident}.pid").write_text(str(stranger.pid))
        stopped = scrills(["run", "nudge", "stop", ident], home, extra_env=env)
        assert stopped.returncode == 0, stopped.stderr
        time.sleep(0.3)
        assert stranger.poll() is None, "stop() signalled an unrelated process"
        assert not (state / f"{ident}.json").exists()
        assert not (state / f"{ident}.pid").exists()
    finally:
        stranger.kill()
        stranger.wait()


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_sigterm_cleans_up(home, tmp_path):
    env, *_ = nudge_env(tmp_path, SCRILLS_WHO="test:term")
    started = scrills(["run", "nudge", "terminate me", "10"], home, extra_env=env)
    assert started.returncode == 0, started.stderr
    ident = started.stdout.split()[1]
    state = Path(home) / ".state" / "nudge"
    pid_file = state / f"{ident}.pid"
    assert wait_for(pid_file.exists)
    pid = int(pid_file.read_text())
    time.sleep(0.5)
    os.kill(pid, signal.SIGTERM)
    assert wait_for(lambda: not (state / f"{ident}.json").exists(), timeout=5), "SIGTERM left the card behind"
    assert wait_for(lambda: not pid_file.exists(), timeout=5)


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_message_from_stdin(home, tmp_path):
    env, speaks, *_ = nudge_env(tmp_path, SCRILLS_WHO="test:stdin")
    started = scrills(["run", "nudge", "--minutes", "10"], home, stdin="private stdin line 9218\n", extra_env=env)
    assert started.returncode == 0, started.stderr
    ident = started.stdout.split()[1]
    assert "every 10m" in started.stdout
    pid = int((Path(home) / ".state" / "nudge" / f"{ident}.pid").read_text())
    args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout
    assert "9218" not in args
    assert wait_for(lambda: "private stdin line 9218" in lines(speaks))
    assert "--minutes" not in lines(speaks)
    asked = scrills(["run", "nudge", "ask"], home, stdin="Stdin question?\n", extra_env=env)
    assert asked.returncode == 0, asked.stderr
    assert asked.stdout == "yes please\n"


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_ask_returns_the_typed_answer_and_stops_nudging(home, tmp_path):
    env, _, _, dialogs = nudge_env(tmp_path, SCRILLS_WHO="test:ask")
    result = scrills(["run", "nudge", "ask", 'Deploy "now"?'], home, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "yes please\n"
    script = dialogs.read_text()
    assert 'display dialog "Deploy \\"now\\"?"' in script
    assert "giving up after 540" in script
    assert nudge_cards(home) == []


@pytest.mark.usefixtures("quiet_nudges")
def test_nudge_ask_choices_timeout_and_cancel(home, tmp_path):
    env, _, _, dialogs = nudge_env(tmp_path, SCRILLS_WHO="test:ask2")
    picked = scrills(["run", "nudge", "ask", "Which one?", "--choices", "a,b"], home, extra_env=env)
    assert picked.returncode == 0, picked.stderr
    assert picked.stdout == "b\n"
    assert 'choose from list {"a", "b"}' in dialogs.read_text()
    gave_up = scrills(["run", "nudge", "ask", "Anyone?", "--timeout", "5"], home, extra_env={**env, "STUB_DIALOG": "gaveup"})
    assert gave_up.returncode == 1
    assert "no answer" in gave_up.stderr
    assert "giving up after 5" in dialogs.read_text()
    cancelled = scrills(["run", "nudge", "ask", "Cancel me?"], home, extra_env={**env, "STUB_DIALOG": "cancel"})
    assert cancelled.returncode == 1
    assert nudge_cards(home) == []


def audit_project(tmp_path):
    layer = tmp_path / "project" / ".scrills"
    layer.mkdir(parents=True)
    for name in ("audit", "subagent"):
        (layer / name).symlink_to(EXAMPLES / ".scrills" / name)
    victim = layer / "victim"
    victim.mkdir()
    (victim / "__init__.py").write_text('"""\n---\nname: victim\ndescription: t\nversion: 0.0.1\n---\n"""\n\n\ndef hello():\n    return "hi"\n')
    return layer.parent, victim


def test_audit_reviews_once_until_the_code_changes(home, tmp_path, clean_state):
    clean_state("audit")
    env, log = claude_env(tmp_path)
    project, victim = audit_project(tmp_path)
    first = scrills(["run", "audit", "victim"], home, extra_env=env, cwd=project)
    assert first.returncode == 0, first.stderr
    assert "audit: victim - ok (reviewed now" in first.stdout
    (call,) = calls(log)
    argv = call["argv"]
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    for flag in ("--restricted", "--strict-mcp-config", "--json-schema", "--safe-mode"):
        assert flag in argv
    assert "- __init__.py" in call["task"]
    assert call["cwd"] == str(victim.resolve())
    second = scrills(["run", "audit", "victim"], home, extra_env=env, cwd=project)
    assert "ok (cached" in second.stdout
    assert len(calls(log)) == 1
    (victim / "helper.py").write_text("X = 1\n")
    third = scrills(["run", "audit", "victim"], home, extra_env=env, cwd=project)
    assert "reviewed now" in third.stdout
    assert len(calls(log)) == 2
    assert "- helper.py" in calls(log)[1]["task"]


def test_audit_exit_codes_follow_the_verdict(home, tmp_path, clean_state):
    clean_state("audit")
    project, victim = audit_project(tmp_path)
    danger = {"verdict": "danger", "summary": "sends your keys away", "issues": [{"severity": "high", "file": "__init__.py", "line": 3, "issue": "posts env to a server"}]}
    danger_env, _ = claude_env(tmp_path / "danger", STUB_STRUCTURED=json.dumps(danger))
    result = scrills(["run", "audit", "victim"], home, extra_env=danger_env, cwd=project)
    assert result.returncode == 4
    assert "[high] __init__.py:3 - posts env to a server" in result.stdout
    caution_env, _ = claude_env(tmp_path / "caution", STUB_STRUCTURED=json.dumps({"verdict": "caution", "summary": "s", "issues": []}))
    result = scrills(["run", "audit", "victim", "--fresh"], home, extra_env=caution_env, cwd=project)
    assert result.returncode == 3
    result = scrills(["run", "audit", "nosuch"], home, extra_env=caution_env, cwd=project)
    assert result.returncode == 2
    assert "no scrill named nosuch" in result.stderr
    (victim / "linked").symlink_to(tmp_path)
    result = scrills(["run", "audit", "victim"], home, extra_env=caution_env, cwd=project)
    assert result.returncode == 1
    assert "linked directory" in result.stderr


def test_audit_refuses_linked_file(home, tmp_path, clean_state):
    clean_state("audit")
    env = claude_env(tmp_path)[0]
    project, victim = audit_project(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("outside data")
    (victim / "notes.md").symlink_to(secret)
    result = scrills(["run", "audit", "victim"], home, extra_env=env, cwd=project)
    assert result.returncode == 1
    assert "linked file" in result.stderr


def test_audit_lists_the_scrills_it_uses(home, tmp_path, clean_state):
    clean_state("audit")
    env, _ = claude_env(tmp_path)
    project, victim = audit_project(tmp_path)
    (victim / "__init__.py").write_text(
        '"""\n---\nname: victim\ndescription: t\nversion: 0.0.2\n---\n"""\nfrom scrills import subagent\n\n\ndef hello():\n    return subagent\n'
    )
    first = scrills(["run", "audit", "victim"], home, extra_env=env, cwd=project)
    assert first.returncode == 0, first.stderr
    assert "uses: subagent (not reviewed)" in first.stdout
    reviewed = scrills(["run", "audit", "subagent"], home, extra_env=env, cwd=project)
    assert reviewed.returncode == 0, reviewed.stderr
    second = scrills(["run", "audit", "victim"], home, extra_env=env, cwd=project)
    assert "ok (cached" in second.stdout
    assert "uses: subagent (ok)" in second.stdout


def test_subagent_timeout_lands_in_ledger(home, tmp_path, clean_state):
    clean_state("subagent")
    env = claude_env(tmp_path)[0]
    code = (
        "from scrills import subagent\n"
        "try:\n"
        "    subagent.run('sleep for a while', timeout=0.2)\n"
        "except subagent.SubagentError as error:\n"
        "    print('caught', 'timed out' in str(error))\n"
        "print(subagent.spent('all'))\n"
    )
    result = scrills(["py"], home, stdin=code, extra_env=env)
    assert result.returncode == 0, result.stderr
    assert "caught True" in result.stdout
    assert "'calls': 1" in result.stdout
    entry = json.loads((Path(home) / ".state" / "subagent" / "ledger.jsonl").read_text().splitlines()[-1])
    assert entry["ok"] is False
    assert entry["timeout"] is True
    assert entry["usd"] == 0


def test_audit_all_covers_the_library(home, tmp_path, clean_state):
    clean_state("audit")
    env, log = claude_env(tmp_path)
    project, _ = audit_project(tmp_path)
    result = scrills(["run", "audit", "--all"], home, extra_env=env, cwd=project)
    assert result.returncode == 0, result.stderr
    for name in ("audit", "subagent", "victim"):
        assert f"audit: {name} - ok" in result.stdout
    assert len(calls(log)) == 3


SKILL_DIR = Path(__file__).resolve().parent.parent / "scrills"


def claude_home(tmp_path, settings=None):
    fake = tmp_path / "fakehome"
    (fake / ".claude" / "skills").mkdir(parents=True)
    if settings is not None:
        (fake / ".claude" / "settings.json").write_text(settings)
    return fake, {"HOME": str(fake)}


def settings_of(fake):
    return json.loads((fake / ".claude" / "settings.json").read_text())


def harness_records(home):
    path = Path(home) / ".runs" / "log.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    return [json.loads(line) for line in lines if '"harness_config"' in line]


def test_harness_config_apply_links_and_allows_preserving_settings(home, tmp_path):
    seeded = {"model": "opus", "permissions": {"allow": ["Bash(git status)"], "defaultMode": "default"}, "hooks": {"Stop": []}}
    fake, env = claude_home(tmp_path, json.dumps(seeded))
    result = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    link = fake / ".claude" / "skills" / "scrills"
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(SKILL_DIR)
    data = settings_of(fake)
    assert data["model"] == "opus"
    assert data["hooks"] == {"Stop": []}
    assert data["permissions"]["defaultMode"] == "default"
    assert data["permissions"]["allow"] == ["Bash(git status)", "Bash(scrills:*)"]
    before = (fake / ".claude" / "settings.json").read_bytes()
    again = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert again.returncode == 0
    assert "nothing to change" in again.stdout
    assert (fake / ".claude" / "settings.json").read_bytes() == before


def test_harness_config_status_reports_and_names_its_exit(home, tmp_path):
    _, env = claude_home(tmp_path)
    result = scrills(["run", "harness_config"], home, extra_env=env)
    assert result.returncode == 1
    assert "skill_link: missing" in result.stdout
    assert "permission: absent" in result.stdout
    record = harness_records(home)[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "not configured"
    assert scrills(["run", "harness_config", "apply"], home, extra_env=env).returncode == 0
    done = scrills(["run", "harness_config", "status"], home, extra_env=env)
    assert done.returncode == 0
    assert "skill_link: ok" in done.stdout
    assert "permission: present" in done.stdout


def test_harness_config_undo_removes_only_ours(home, tmp_path):
    seeded = {"permissions": {"allow": ["Bash(git status)"]}, "env": {"KEEP": "1"}}
    fake, env = claude_home(tmp_path, json.dumps(seeded))
    assert scrills(["run", "harness_config", "apply"], home, extra_env=env).returncode == 0
    result = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (fake / ".claude" / "skills" / "scrills").exists()
    data = settings_of(fake)
    assert data["permissions"]["allow"] == ["Bash(git status)"]
    assert data["env"] == {"KEEP": "1"}
    again = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert again.returncode == 0
    assert "nothing to change" in again.stdout


def test_harness_config_never_touches_a_real_folder(home, tmp_path):
    fake, env = claude_home(tmp_path)
    occupied = fake / ".claude" / "skills" / "scrills"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("mine")
    result = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert result.returncode == 1
    assert "not a symlink" in result.stdout
    assert (occupied / "keep.txt").read_text() == "mine"
    assert settings_of(fake)["permissions"]["allow"] == ["Bash(scrills:*)"]
    undone = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert undone.returncode == 0
    assert (occupied / "keep.txt").read_text() == "mine"


def test_harness_config_refuses_corrupt_settings_before_touching_anything(home, tmp_path):
    fake, env = claude_home(tmp_path, "{broken json")
    result = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr
    assert (fake / ".claude" / "settings.json").read_text() == "{broken json"
    assert not (fake / ".claude" / "skills" / "scrills").exists()


def test_harness_config_usage(home, tmp_path):
    _, env = claude_home(tmp_path)
    result = scrills(["run", "harness_config", "configure"], home, extra_env=env)
    assert result.returncode == 2
    assert "usage: scrills run harness_config" in result.stderr
    record = harness_records(home)[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "usage"
