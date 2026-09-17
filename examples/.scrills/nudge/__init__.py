"""
---
name: nudge
description: Use the moment you ask the user a question and start waiting. ask() shows the question in a dialog and returns what they type; start() keeps nudging - aloud when they're away, a quiet notification when they're at the computer - until you stop it.
version: 0.1.3
---

ask(question, choices=None, minutes=5.0, timeout=540) -> the answer, or None when nobody
  answered in time or they cancelled. Nudges while the dialog waits and stops when it closes.
  It blocks the calling command; the default timeout stays under common 10-minute tool
  limits - ask again to keep waiting. Dialogs need macOS (osascript). Keep questions short:
  they are also spoken.
start(message, minutes=5.0) -> an id. A detached nudger: nudges now, then every `minutes`,
  until stopped or after 12 nudges. Stop it as your first action once the user answers.
stop(which=None, everywhere=False) -> the ids stopped: that id; or, with no id, every nudger
  started from this scope ($SCRILLS_WHO when set, else the working directory); or all of them.
status() -> the live nudgers: [{id, message, minutes, started, scope, here}].

Presence decides each nudge: when macOS idle time (ioreg) shows the keyboard or mouse was used
in the last 60 seconds you're here, so a notification (terminal-notifier, else osascript);
otherwise the voice (say, else espeak). Unknown idle time counts as away.
The library and nudge's own child keep the message out of argv - it travels over stdin and in
state files. The program form's command line is visible in ps like any command: pipe the
message on stdin (below) when it's private.
Each nudger holds a kernel lock on its <id>.lock for its whole life; stop() signals only while
that lock is held, so a stale card after a crash or reboot can never make it signal a stranger.
State: ~/.scrills/.state/nudge/ - <id>.json while active, <id>.pid, <id>.lock, and a log with
no messages.

As a program: scrills run nudge ask "<question>" [--choices a,b,c] [--timeout s] [--minutes m]
| scrills run nudge "<message>" [minutes] | echo "<message>" | scrills run nudge [--minutes m]
| echo "<question>" | scrills run nudge ask [flags] | scrills run nudge stop [id|--all]
| scrills run nudge status
Exit codes for ask: 0 answered (printed), 1 no answer, 2 usage or no dialog support.
"""
import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time

HOME = os.path.abspath(os.path.expanduser(os.environ.get("SCRILLS_HOME", "").strip() or "~/.scrills"))
STATE_DIR = os.path.join(HOME, ".state", "nudge")
LOG = os.path.join(STATE_DIR, "log")
LOG_CAP = 1 << 20
CAP = 12
MINUTES = 5.0
PRESENT_SECONDS = 60
ASK_TIMEOUT = 540
ANSWER = "ANSWER:"
USAGE = (
    'usage: scrills run nudge ask "<question>" [--choices a,b,c] [--timeout s] [--minutes m]\n'
    '  scrills run nudge "<message>" [minutes] | scrills run nudge stop [id|--all] | scrills run nudge status\n'
    '  private text goes over stdin instead: echo "<message>" | scrills run nudge [--minutes m]  (also: ... | scrills run nudge ask [flags])'
)


def _card_path(ident):
    return os.path.join(STATE_DIR, f"{ident}.json")


def _pid_path(ident):
    return os.path.join(STATE_DIR, f"{ident}.pid")


def _lock_path(ident):
    return os.path.join(STATE_DIR, f"{ident}.lock")


def _write(path, text):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _load(ident):
    try:
        with open(_card_path(ident), encoding="utf-8") as handle:
            card = json.load(handle)
    except (OSError, ValueError):
        return None
    return card if isinstance(card, dict) else None


def _pid(ident):
    try:
        with open(_pid_path(ident), encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def _discard(ident):
    for path in (_card_path(ident), _pid_path(ident), _lock_path(ident)):
        try:
            os.remove(path)
        except OSError:
            pass


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _held(ident):
    try:
        fd = os.open(_lock_path(ident), os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            pid = _pid(ident)
            return pid is not None and _alive(pid)
        return False
    finally:
        os.close(fd)


def _scope():
    return os.environ.get("SCRILLS_WHO", "").strip() or os.path.realpath(os.getcwd())


def _cards():
    try:
        names = sorted(os.listdir(STATE_DIR))
    except OSError:
        return []
    found = []
    for filename in names:
        stem, extension = os.path.splitext(filename)
        if extension == ".json" and re.fullmatch(r"[0-9a-f]{6}", stem):
            card = _load(stem)
            if card is not None and card.get("id") == stem:
                found.append(card)
    return found


def _applescript(value):
    clean = "".join(char if char in "\n\t" or ord(char) >= 32 else " " for char in str(value))
    return json.dumps(clean, ensure_ascii=False)


def idle_seconds():
    """Seconds since the last keyboard or mouse input (macOS ioreg), or None when unknown."""
    tool = shutil.which("ioreg")
    if tool is None:
        return None
    try:
        output = subprocess.run([tool, "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', output)
    return int(match.group(1)) / 1e9 if match else None


def _feed(command, message):
    subprocess.run(command, input=message, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def _notify(message):
    notifier = shutil.which("terminal-notifier")
    if notifier:
        _feed([notifier, "-title", "nudge", "-sound", "default"], message)
        return "terminal-notifier"
    osascript = shutil.which("osascript")
    if osascript:
        _feed([osascript, "-"], f'display notification {_applescript(message)} with title "nudge"')
        return "osascript"
    return None


def _speak(message):
    say = shutil.which("say")
    if say:
        _feed([say], message)
        return "say"
    espeak = shutil.which("espeak")
    if espeak:
        _feed([espeak, "--stdin"], message)
        return "espeak"
    return None


def _nudge(message):
    idle = idle_seconds()
    if idle is not None and idle < PRESENT_SECONDS:
        way = _notify(message)
        if way:
            return way
    return _speak(message) or "nothing (no voice or notifier found)"


def start(message, minutes=MINUTES):
    """Detach a nudger and return its id at once; the calling command never waits."""
    os.makedirs(STATE_DIR, exist_ok=True)
    ident = secrets.token_hex(3)
    while os.path.exists(_card_path(ident)):
        ident = secrets.token_hex(3)
    card = {"id": ident, "message": str(message), "minutes": float(minutes), "scope": _scope(), "started": time.time()}
    _write(_card_path(ident), json.dumps(card, ensure_ascii=False))
    with open(_lock_path(ident), "w", encoding="utf-8"):
        pass
    try:
        if os.path.getsize(LOG) > LOG_CAP:
            os.remove(LOG)
    except OSError:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-X",
                    "pycache_prefix=" + os.path.join(HOME, ".pycache"),
                    "-c",
                    "from scrills import nudge; nudge._loop()",
                    ident,
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
    except OSError:
        _discard(ident)
        raise
    _write(_pid_path(ident), str(child.pid))
    if _load(ident) is None:
        _discard(ident)
    return ident


def _halt(ident):
    pid = _pid(ident)
    holding = _held(ident)
    _discard(ident)
    if holding and pid is not None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass


def stop(which=None, everywhere=False):
    """Stop nudgers: the id given, else this scope's, else (everywhere=True) all. Returns the ids."""
    scope = _scope()
    stopped = []
    for card in _cards():
        ident = card.get("id")
        if which is not None and ident != which:
            continue
        if which is None and not everywhere and card.get("scope") != scope:
            continue
        _halt(ident)
        stopped.append(ident)
    return stopped


def status():
    """The live nudgers, each marked here=True when started from this scope; prunes dead ones."""
    scope = _scope()
    live = []
    for card in _cards():
        ident = card.get("id")
        young = time.time() - float(card.get("started") or 0) < 10
        if not _held(ident) and not young:
            _discard(ident)
            continue
        live.append({**card, "here": card.get("scope") == scope})
    return live


def _dialog_script(question, choices, timeout):
    if choices:
        listed = ", ".join(_applescript(choice) for choice in choices)
        return (
            "activate\n"
            f'set picked to choose from list {{{listed}}} with title "nudge" with prompt {_applescript(question)}\n'
            'if picked is false then return "CANCELLED"\n'
            f'return "{ANSWER}" & (item 1 of picked)\n'
        )
    return (
        "activate\n"
        f'set reply to display dialog {_applescript(question)} with title "nudge" default answer "" '
        f'buttons {{"Cancel", "Reply"}} default button "Reply" cancel button "Cancel" giving up after {int(timeout)}\n'
        'if gave up of reply then return "GAVE-UP"\n'
        f'return "{ANSWER}" & (text returned of reply)\n'
    )


def ask(question, choices=None, minutes=MINUTES, timeout=ASK_TIMEOUT):
    """Show the question in a dialog while nudging; the answer as a string, or None."""
    osascript = shutil.which("osascript")
    if osascript is None:
        raise RuntimeError("nudge: ask needs macOS dialogs (osascript) - use start() and read the answer in chat")
    ident = start(question, minutes)
    try:
        wait = float(timeout) if choices else float(timeout) + 15
        try:
            done = subprocess.run([osascript, "-"], input=_dialog_script(question, choices, timeout), capture_output=True, text=True, timeout=wait)
        except subprocess.TimeoutExpired:
            return None
        output = done.stdout
        if done.returncode != 0 or not output.startswith(ANSWER):
            return None
        answer = output[len(ANSWER) :]
        return answer[:-1] if answer.endswith("\n") else answer
    finally:
        stop(ident)


def _loop():
    ident = sys.argv[1]
    card = _load(ident)
    if card is None:
        return
    try:
        lock = os.open(_lock_path(ident), os.O_RDWR | os.O_CREAT)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock = None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    message = card.get("message", "")
    minutes = float(card.get("minutes", MINUTES))
    try:
        for count in range(CAP):
            if count:
                time.sleep(minutes * 60)
            if _load(ident) is None:
                return
            way = _nudge(message)
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {ident} nudged via {way} ({count + 1}/{CAP})", flush=True)
    finally:
        if _pid(ident) in (os.getpid(), None):
            _discard(ident)
        if lock is not None:
            os.close(lock)


def _age(started):
    seconds = max(0, int(time.time() - float(started or 0)))
    return f"{seconds // 60}m" if seconds >= 60 else f"{seconds}s"


EXITS = {1: "no answer", 2: "usage"}


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--minutes":
        minutes = MINUTES
        if args:
            if len(args) != 2:
                print(USAGE, file=sys.stderr)
                return 2
            try:
                minutes = float(args[1])
            except ValueError:
                print(f"nudge: --minutes takes a number, got {args[1]}", file=sys.stderr)
                return 2
        piped = None if sys.stdin.isatty() else sys.stdin.read().strip()
        if not piped:
            print(USAGE, file=sys.stderr)
            return 2
        ident = start(piped, minutes)
        print(f"nudge: {ident} nudging every {minutes:g}m (cap {CAP}) - stop with: scrills run nudge stop {ident}")
        return 0
    verb = args[0]
    if verb == "stop":
        rest = args[1:]
        if rest == ["--all"]:
            stopped = stop(everywhere=True)
        elif len(rest) == 1:
            stopped = stop(rest[0])
        elif not rest:
            stopped = stop()
        else:
            print(USAGE, file=sys.stderr)
            return 2
        print(f"nudge: stopped {', '.join(stopped)}" if stopped else "nudge: nothing to stop")
        return 0
    if verb == "status":
        live = status()
        if not live:
            print("nudge: nothing running")
        for card in live:
            where = "" if card["here"] else f"  [from {card.get('scope')}]"
            print(f"nudge: {card['id']} every {card.get('minutes', MINUTES):g}m, {_age(card.get('started'))} old: {card.get('message', '')}{where}")
        return 0
    if verb == "ask":
        rest = args[1:]
        options = {"choices": None, "timeout": ASK_TIMEOUT, "minutes": MINUTES}
        question = None
        try:
            while rest:
                item = rest.pop(0)
                if item in ("--choices", "--timeout", "--minutes"):
                    if not rest:
                        print(f"nudge: {item} needs a value", file=sys.stderr)
                        return 2
                    value = rest.pop(0)
                    if item == "--choices":
                        options["choices"] = [choice.strip() for choice in value.split(",") if choice.strip()]
                    else:
                        options[item[2:]] = float(value)
                elif question is None:
                    question = item
                else:
                    print(USAGE, file=sys.stderr)
                    return 2
        except ValueError:
            print("nudge: --timeout and --minutes take numbers", file=sys.stderr)
            return 2
        if not question and not sys.stdin.isatty():
            question = sys.stdin.read().strip()
        if not question:
            print(USAGE, file=sys.stderr)
            return 2
        try:
            answer = ask(question, **options)
        except RuntimeError as error:
            print(error, file=sys.stderr)
            return 2
        if answer is None:
            print("nudge: no answer (timed out or cancelled) - ask again to keep waiting", file=sys.stderr)
            return 1
        print(answer)
        return 0
    minutes = MINUTES
    if len(args) > 2:
        print(USAGE, file=sys.stderr)
        return 2
    if len(args) == 2:
        try:
            minutes = float(args[1])
        except ValueError:
            print(f"nudge: minutes must be a number, got {args[1]}", file=sys.stderr)
            return 2
    ident = start(verb, minutes)
    print(f"nudge: {ident} nudging every {minutes:g}m (cap {CAP}) - stop with: scrills run nudge stop {ident}")
    return 0
