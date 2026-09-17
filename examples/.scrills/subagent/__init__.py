"""
---
name: subagent
description: Use when work is worth handing to a fresh claude session - an isolated job whose details would pollute your context, many jobs to run in parallel, or a conversation to keep across calls. Costs real tokens; every call lands in a spend ledger.
version: 0.1.2
---

run(task, **options) -> the final text; with schema={...} (a JSON Schema) the parsed object.
full(task, **options) -> the whole result envelope (result, session_id, total_cost_usd, ...).
map(tasks, limit=4, total_usd=None, **options) -> answers in task order. A failed task's slot
  holds its SubagentError, so one failure never discards answers already paid for; total_usd
  stops starting new tasks once that much is spent.
session(name) -> a conversation that outlives the call: .ask(task, **options), .fork(new_name),
  .forget(). sessions() lists them. One ask at a time per session - fork to branch.
spent(period="today") -> {"usd", "calls", "since"}; period is today, week, month or all.
Async twins for py snippets that await: arun, afull, amap, Session.aask. Sync functions use
threads, never asyncio.run, so they also work inside a snippet that awaits.

Options, with defaults that keep delegation cheap:
  model="sonnet" (None = claude's configured default)
  lean=True - claude --safe-mode: no CLAUDE.md, skills, plugins, hooks or MCP servers; about
    half the context of a full start. lean=False loads your whole setup.
  max_budget_usd=1.0 per call (None = uncapped)
  tools - the only tools that exist, e.g. "Read,Glob,Grep" ("" = none)
  allowed_tools - tools approved without asking, e.g. "Read,Edit"
  permission_mode - e.g. "acceptEdits"
  cwd (default yours), max_turns, timeout (seconds), schema, extra (raw claude flags)
A subagent starts with nothing approved: headless claude cannot ask, so a tool that needs
approval just fails. Granting is always the caller's explicit decision.

The library keeps the task out of argv - it reaches claude over stdin. The program form's own
command line (`scrills run subagent <task>`) is visible in ps like any command: pipe the task
on stdin instead when it's private. The ledger (~/.scrills/.state/subagent/ledger.jsonl) and
session cards hold metadata only - never task text or answers. A timed-out call is recorded
with usd 0 and timeout true (its real spend never arrived), and processes the killed claude
had itself started may linger briefly.

As a program: scrills run subagent [--model m] [--no-lean] [--budget usd|none] [--tools t]
[--allowed-tools t] [--permission-mode m] [--max-turns n] [--timeout s] [--cwd d]
[--schema json] [--session name] [--full] <task words...>
scrills run subagent --spent [today|week|month|all] | --sessions | --forget <name>
"""
import asyncio
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

MODEL = "sonnet"
BUDGET_USD = 1.0
LIMIT = 4
HOME = os.path.abspath(os.path.expanduser(os.environ.get("SCRILLS_HOME", "").strip() or "~/.scrills"))
STATE_DIR = os.path.join(HOME, ".state", "subagent")
LEDGER = os.path.join(STATE_DIR, "ledger.jsonl")
SESSIONS_DIR = os.path.join(STATE_DIR, "sessions")
PERIODS = ("today", "week", "month", "all")
SESSION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DEFAULTS = {
    "model": MODEL,
    "lean": True,
    "max_budget_usd": BUDGET_USD,
    "tools": None,
    "allowed_tools": None,
    "permission_mode": None,
    "cwd": None,
    "max_turns": None,
    "timeout": None,
    "schema": None,
    "extra": (),
}
FLAGS = {
    "--model": "model",
    "--budget": "max_budget_usd",
    "--tools": "tools",
    "--allowed-tools": "allowed_tools",
    "--permission-mode": "permission_mode",
    "--max-turns": "max_turns",
    "--timeout": "timeout",
    "--cwd": "cwd",
    "--schema": "schema",
}
USAGE = (
    "usage: scrills run subagent [--model m] [--no-lean] [--budget usd|none] [--tools t] [--allowed-tools t]\n"
    "  [--permission-mode m] [--max-turns n] [--timeout s] [--cwd d] [--schema json] [--session name] [--full] <task>\n"
    "  scrills run subagent --spent [today|week|month|all] | --sessions | --forget <name>"
)


class SubagentError(RuntimeError):
    """A failed subagent call. .envelope holds claude's result envelope when there was one."""

    def __init__(self, message, envelope=None):
        super().__init__(message)
        self.envelope = envelope


def _now():
    return datetime.datetime.now().astimezone()


def _options(given):
    unknown = sorted(set(given) - set(DEFAULTS))
    if unknown:
        raise TypeError(f"subagent: unknown option {unknown[0]} (known: {', '.join(DEFAULTS)})")
    merged = dict(DEFAULTS)
    merged.update(given)
    return merged


def _command(options, resume=None, fork=False):
    claude = shutil.which("claude")
    if claude is None:
        raise SubagentError("subagent: no 'claude' on PATH - install Claude Code first")
    command = [claude, "-p", "--output-format", "json"]
    if options["model"]:
        command += ["--model", options["model"]]
    if options["lean"]:
        command.append("--safe-mode")
    if options["max_budget_usd"] is not None:
        command += ["--max-budget-usd", str(options["max_budget_usd"])]
    if options["tools"] is not None:
        command += ["--tools", options["tools"]]
    if options["allowed_tools"]:
        command += ["--allowedTools", options["allowed_tools"]]
    if options["permission_mode"]:
        command += ["--permission-mode", options["permission_mode"]]
    if options["max_turns"]:
        command += ["--max-turns", str(options["max_turns"])]
    if options["schema"] is not None:
        schema = options["schema"]
        command += ["--json-schema", schema if isinstance(schema, str) else json.dumps(schema)]
    if resume:
        command += ["--resume", resume]
        if fork:
            command.append("--fork-session")
    return command + list(options["extra"])


def _append_ledger(entry, session):
    if session:
        entry["session"] = session
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _record(envelope, options, session):
    _append_ledger(
        {
            "at": _now().isoformat(timespec="seconds"),
            "usd": envelope.get("total_cost_usd") or 0,
            "requested": options["model"] or "default",
            "models": sorted(envelope.get("modelUsage") or {}),
            "turns": envelope.get("num_turns"),
            "ms": envelope.get("duration_ms"),
            "ok": not envelope.get("is_error"),
        },
        session,
    )


def _record_timeout(options, session):
    _append_ledger(
        {
            "at": _now().isoformat(timespec="seconds"),
            "usd": 0,
            "requested": options["model"] or "default",
            "models": [],
            "turns": None,
            "ms": int(float(options["timeout"]) * 1000),
            "ok": False,
            "timeout": True,
        },
        session,
    )


def _failure(envelope, returncode, options):
    subtype = envelope.get("subtype") or "error"
    if subtype == "error_max_budget_usd":
        return f"subagent: stopped at the ${options['max_budget_usd']} budget (max_budget_usd)"
    if subtype == "error_max_turns":
        return f"subagent: stopped after max_turns={options['max_turns']}"
    if subtype == "success":
        subtype = "error"
    detail = envelope.get("result") or "; ".join(str(item) for item in envelope.get("errors") or [])
    return f"subagent: {subtype}: {str(detail or f'claude exited {returncode}')[-2000:]}"


def _settle(returncode, stdout, stderr, options, session):
    try:
        envelope = json.loads(stdout)
    except ValueError:
        envelope = None
    if not isinstance(envelope, dict):
        tail = (stderr or stdout or "").strip()[-2000:]
        raise SubagentError(f"subagent: claude exited {returncode} without a result envelope: {tail}")
    _record(envelope, options, session)
    if envelope.get("is_error") or returncode != 0:
        raise SubagentError(_failure(envelope, returncode, options), envelope)
    return envelope


def _answer(envelope, options):
    if options["schema"] is None:
        return envelope.get("result", "")
    if envelope.get("structured_output") is None:
        raise SubagentError("subagent: a schema was given but claude returned no structured_output", envelope)
    return envelope["structured_output"]


def _full(task, options, resume=None, fork=False, session=None):
    command = _command(options, resume, fork)
    timeout = float(options["timeout"]) if options["timeout"] else None
    try:
        done = subprocess.run(command, input=task, cwd=options["cwd"], timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        _record_timeout(options, session)
        raise SubagentError(f"subagent: timed out after {options['timeout']}s") from None
    return _settle(done.returncode, done.stdout, done.stderr, options, session)


async def _afull(task, options, resume=None, fork=False, session=None):
    command = _command(options, resume, fork)
    timeout = float(options["timeout"]) if options["timeout"] else None
    child = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=options["cwd"],
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(task.encode("utf-8")), timeout)
    except asyncio.TimeoutError:
        _record_timeout(options, session)
        raise SubagentError(f"subagent: timed out after {options['timeout']}s") from None
    finally:
        if child.returncode is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            await child.wait()
    return _settle(child.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"), options, session)


def full(task, **options):
    """The whole result envelope. Raises SubagentError when claude fails or reports an error."""
    return _full(task, _options(options))


def run(task, **options):
    """The final text - or, with schema=, the parsed structured output."""
    merged = _options(options)
    return _answer(_full(task, merged), merged)


async def afull(task, **options):
    """Async twin of full()."""
    return await _afull(task, _options(options))


async def arun(task, **options):
    """Async twin of run()."""
    merged = _options(options)
    return _answer(await _afull(task, merged), merged)


class _Spend:
    def __init__(self, cap):
        self.cap = cap
        self.usd = 0.0
        self.lock = threading.Lock()

    def add(self, envelope):
        if envelope:
            with self.lock:
                self.usd += float(envelope.get("total_cost_usd") or 0)

    def refusal(self):
        with self.lock:
            if self.cap is not None and self.usd >= self.cap:
                return SubagentError(f"subagent: not started - this map already spent ${self.usd:.2f} of total_usd={self.cap}")
        return None


def _settled_answer(envelope, options, spend):
    spend.add(envelope)
    try:
        return _answer(envelope, options)
    except SubagentError as error:
        return error


def map(tasks, limit=LIMIT, total_usd=None, **options):
    """Run many tasks, `limit` at a time, on threads. Answers in task order; failures in place."""
    merged = _options(options)
    spend = _Spend(total_usd)

    def one(task):
        refused = spend.refusal()
        if refused is not None:
            return refused
        try:
            envelope = _full(task, merged)
        except SubagentError as error:
            spend.add(error.envelope)
            return error
        except OSError as error:
            return SubagentError(f"subagent: {error}")
        return _settled_answer(envelope, merged, spend)

    with ThreadPoolExecutor(max_workers=max(1, int(limit))) as pool:
        return list(pool.map(one, list(tasks)))


async def amap(tasks, limit=LIMIT, total_usd=None, **options):
    """Async twin of map(): same answers, one event loop, no threads."""
    merged = _options(options)
    spend = _Spend(total_usd)
    gate = asyncio.Semaphore(max(1, int(limit)))

    async def one(task):
        async with gate:
            refused = spend.refusal()
            if refused is not None:
                return refused
            try:
                envelope = await _afull(task, merged)
            except SubagentError as error:
                spend.add(error.envelope)
                return error
            except OSError as error:
                return SubagentError(f"subagent: {error}")
            return _settled_answer(envelope, merged, spend)

    return list(await asyncio.gather(*(one(task) for task in tasks)))


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


class Session:
    """A named conversation kept in ~/.scrills/.state/subagent/sessions/<name>.json."""

    def __init__(self, name):
        if not isinstance(name, str) or not SESSION_NAME.match(name):
            raise ValueError(f"subagent: session names are letters, digits, . _ - (got {name!r})")
        self.name = name
        self.path = os.path.join(SESSIONS_DIR, name + ".json")

    def __repr__(self):
        return f"subagent.session({self.name!r})"

    def card(self):
        """The stored card - session_id, cwd, asks, usd, created, updated - or None before the first ask."""
        return _read_json(self.path)

    def _prepare(self, options):
        merged = _options(options)
        card = self.card() or {}
        home = card.get("cwd")
        wanted = os.path.realpath(merged["cwd"] or os.getcwd())
        if home and merged["cwd"] and wanted != home:
            raise ValueError(f"subagent: session {self.name} lives in {home}; claude resumes a session only from there")
        merged["cwd"] = home or wanted
        if card.get("fork_of"):
            return merged, card, card["fork_of"], True
        return merged, card, card.get("session_id"), False

    def _remember(self, card, envelope, options):
        now = _now().isoformat(timespec="seconds")
        updated = dict(card)
        updated.pop("fork_of", None)
        updated.update(
            name=self.name,
            session_id=envelope.get("session_id") or card.get("session_id"),
            cwd=options["cwd"],
            created=card.get("created") or now,
            updated=now,
            asks=int(card.get("asks") or 0) + 1,
            usd=round(float(card.get("usd") or 0) + float(envelope.get("total_cost_usd") or 0), 6),
        )
        _write_json(self.path, updated)

    def _exchange(self, task, options):
        merged, card, resume, fork = self._prepare(options)
        try:
            envelope = _full(task, merged, resume, fork, self.name)
        except SubagentError as error:
            if error.envelope and error.envelope.get("session_id"):
                self._remember(card, error.envelope, merged)
            raise
        self._remember(card, envelope, merged)
        return envelope, merged

    async def _aexchange(self, task, options):
        merged, card, resume, fork = self._prepare(options)
        try:
            envelope = await _afull(task, merged, resume, fork, self.name)
        except SubagentError as error:
            if error.envelope and error.envelope.get("session_id"):
                self._remember(card, error.envelope, merged)
            raise
        self._remember(card, envelope, merged)
        return envelope, merged

    def ask(self, task, **options):
        """Continue the conversation (or start it); returns the answer like run()."""
        envelope, merged = self._exchange(task, options)
        return _answer(envelope, merged)

    async def aask(self, task, **options):
        """Async twin of ask()."""
        envelope, merged = await self._aexchange(task, options)
        return _answer(envelope, merged)

    def fork(self, new_name):
        """A new session that starts from this conversation's current state; this one stays as it was."""
        card = self.card()
        if not card or not card.get("session_id"):
            raise ValueError(f"subagent: session {self.name} has no conversation to fork yet - ask it something first")
        branch = Session(new_name)
        if branch.card() is not None:
            raise ValueError(f"subagent: session {new_name} already exists - forget() it first or pick another name")
        now = _now().isoformat(timespec="seconds")
        _write_json(branch.path, {"name": new_name, "fork_of": card["session_id"], "cwd": card.get("cwd"), "created": now, "updated": now, "asks": 0, "usd": 0})
        return branch

    def forget(self):
        """Drop the card; claude's own transcript stays wherever claude keeps it."""
        try:
            os.remove(self.path)
            return True
        except OSError:
            return False


def session(name):
    """The named conversation - created on its first ask."""
    return Session(name)


def sessions():
    """Every stored session card, most recently updated first."""
    try:
        names = os.listdir(SESSIONS_DIR)
    except OSError:
        return []
    cards = [_read_json(os.path.join(SESSIONS_DIR, filename)) for filename in names if filename.endswith(".json")]
    return sorted((card for card in cards if card), key=lambda card: card.get("updated") or "", reverse=True)


def spent(period="today"):
    """{"usd", "calls", "since"} summed from the ledger - today, week, month or all."""
    if period not in PERIODS:
        raise ValueError(f"subagent: period is one of {', '.join(PERIODS)} (got {period!r})")
    now = _now()
    since = None
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - datetime.timedelta(days=7)
    elif period == "month":
        since = now - datetime.timedelta(days=30)
    usd, calls = 0.0, 0
    try:
        with open(LEDGER, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            entry = json.loads(line)
            at = datetime.datetime.fromisoformat(entry["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if since is not None and at < since:
            continue
        usd += float(entry.get("usd") or 0)
        calls += 1
    return {"usd": round(usd, 4), "calls": calls, "since": since.isoformat(timespec="seconds") if since else None}


def _flag_value(key, raw):
    if key == "max_budget_usd":
        return None if raw.lower() == "none" else float(raw)
    if key == "max_turns":
        return int(raw)
    if key == "timeout":
        return float(raw)
    return raw


def main():
    args = sys.argv[1:]
    try:
        if args[:1] == ["--spent"]:
            period = args[1] if len(args) > 1 else "today"
            total = spent(period)
            print(f"subagent: ${total['usd']:.2f} across {total['calls']} calls ({period})")
            return 0
        if args[:1] == ["--sessions"]:
            cards = sessions()
            if not cards:
                print("subagent: no sessions")
            for card in cards:
                print(f"{card.get('name')}  {card.get('asks', 0)} asks  ${float(card.get('usd') or 0):.2f}  updated {card.get('updated')}  {card.get('cwd')}")
            return 0
        if args[:1] == ["--forget"]:
            if len(args) != 2:
                print(USAGE, file=sys.stderr)
                return 2
            print(f"subagent: forgot {args[1]}" if session(args[1]).forget() else f"subagent: no session {args[1]}")
            return 0
        options, name, show_full = {}, None, False
        while args and args[0].startswith("--"):
            flag = args.pop(0)
            if flag == "--full":
                show_full = True
            elif flag == "--no-lean":
                options["lean"] = False
            elif flag in FLAGS or flag == "--session":
                if not args:
                    print(f"subagent: {flag} needs a value", file=sys.stderr)
                    return 2
                value = args.pop(0)
                if flag == "--session":
                    name = value
                else:
                    options[FLAGS[flag]] = _flag_value(FLAGS[flag], value)
            else:
                print(f"subagent: unknown flag {flag}\n{USAGE}", file=sys.stderr)
                return 2
        task = " ".join(args).strip()
        if not task and not sys.stdin.isatty():
            task = sys.stdin.read().strip()
        if not task:
            print(USAGE, file=sys.stderr)
            return 2
        if name is not None:
            envelope, merged = session(name)._exchange(task, options)
        else:
            merged = _options(options)
            envelope = _full(task, merged)
        if show_full:
            print(json.dumps(envelope, indent=2, ensure_ascii=False))
        else:
            answer = _answer(envelope, merged)
            print(answer if isinstance(answer, str) else json.dumps(answer, indent=2, ensure_ascii=False))
    except (SubagentError, ValueError, TypeError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0
