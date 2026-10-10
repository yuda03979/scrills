"""Automatic response storage and bounded, non-executing history reads.

start() redirects output to private files before user code; no live copying.
finish() publishes the runner's outcome independently of the rotating registry.
echo() saves the full representation and records its display-only cap.
show() reads bytes/lines from stored stdout then stderr, never importing a scrill.
deliver() returns the files synchronously during the run's existing finalization.
There is no output helper process, live forwarding or per-byte background work.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import stat
import sys


ID = re.compile(r"[0-9a-f]{16}\Z")
STREAMS = ("stdout", "stderr")
DEFAULT_LIMIT = 8192
MAX_LIMIT = 65536
META_LIMIT = 262144


def atomic_json(path, value):
    """Publish a private JSON snapshot without exposing half-written metadata."""
    tmp = path + "." + str(os.getpid()) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=True)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def open_regular(path):
    """Open a regular, non-symlink file without waiting on FIFOs or devices."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_meta(path, ident):
    """Validate an independently retained metadata snapshot, with bounded reads."""
    with open_regular(path) as handle:
        data = handle.read(META_LIMIT + 1)
    if len(data) > META_LIMIT:
        raise ValueError("metadata is too large")
    value = json.loads(data)
    if not isinstance(value, dict) or value.get("id") != ident:
        raise ValueError("metadata has the wrong shape or run ID")
    return value


def write_all(fd, data):
    """Write all bytes, including short writes; use no Python stream buffering."""
    remaining = memoryview(data)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            raise OSError("short output write")
        remaining = remaining[count:]


def private_dir(path, create=False):
    """Refuse directory links/foreign ownership; newly managed directories are private."""
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("history must be a directory owned by you, not a symlink")
    if create:
        os.chmod(path, 0o700)


def high_dup(fd):
    """Keep internal descriptors above stdio, even when the caller closed fd 1/2."""
    copied = fcntl.fcntl(fd, fcntl.F_DUPFD, 3)
    os.set_inheritable(copied, False)
    return copied


def delivery_fd(fd):
    """Preserve the caller's destination; a closed stream remains undisplayed."""
    stream = sys.stdout if fd == 1 else sys.stderr
    if stream is not None:
        try:
            return high_dup(fd)
        except OSError:
            pass
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        return high_dup(devnull)
    finally:
        os.close(devnull)


def start(home, info):
    """Redirect to files and carry the caller's destinations through the existing execv."""
    root = os.path.join(home, ".results")
    private_dir(root, create=True)
    ignore = os.path.join(root, ".gitignore")
    try:
        fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as handle:
            handle.write("*\n")
    directory = os.path.join(root, info["id"])
    os.mkdir(directory, 0o700)
    atomic_json(os.path.join(directory, "meta.json"), {**info, "complete": False})
    context = {"directory": directory, "id": info["id"]}
    writers, destinations, redirected = [], [], []
    try:
        for number in (1, 2):
            destinations.append(delivery_fd(number))
        for name in STREAMS:
            fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                writers.append(high_dup(fd))
            finally:
                os.close(fd)
        for number, stream in enumerate((sys.stdout, sys.stderr), 1):
            if stream is not None:
                stream.flush()
                os.dup2(writers[number - 1], number)
                redirected.append(number)
        for fd in destinations:
            os.set_inheritable(fd, True)
        context["destinations"] = destinations
        return context
    except BaseException:
        for number in redirected:
            os.dup2(destinations[number - 1], number)
        for fd in destinations:
            os.close(fd)
        raise
    finally:
        for fd in writers:
            os.close(fd)


def flush_response():
    """Flush Python and native stdio before reading the completed response on POSIX."""
    errors = {}
    for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
        if stream is not None and not getattr(stream, "closed", False):
            try:
                stream.flush()
            except (OSError, ValueError, AttributeError) as error:
                errors[name] = type(error).__name__
    try:
        import ctypes
        if ctypes.CDLL(None).fflush(None) != 0:
            errors["stdio"] = "flush failed"
    except (OSError, AttributeError, ImportError) as error:
        errors["stdio"] = type(error).__name__
    return errors


def finish(context, record):
    """Publish the saved response independently of the rotating run registry."""
    errors = flush_response()
    atomic_json(os.path.join(context["directory"], "meta.json"),
                {**record, "complete": not errors, "capture_errors": errors})


def echo(context, full, displayed, stream):
    """Append the uncapped value; record only its own byte spans for capped presentation.

    A separate O_APPEND descriptor supplies exact end offsets even when other
    stdout writers interleave. No marker or special bytes enter the response.
    """
    stream.flush()
    destination = os.stat(os.path.join(context["directory"], "stdout"), follow_symlinks=False)
    current = os.fstat(1)
    if (current.st_dev, current.st_ino) != (destination.st_dev, destination.st_ino):
        print(displayed, file=stream)
        return
    encoding, errors = stream.encoding or "utf-8", stream.errors or "strict"
    data = memoryview((full + "\n").encode(encoding, errors))
    spans = []
    fd = os.open(os.path.join(context["directory"], "stdout"), os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        while data:
            count = os.write(fd, data)
            if count <= 0:
                raise OSError("short echo write")
            end = os.lseek(fd, 0, os.SEEK_CUR)
            if spans and spans[-1][1] == end - count:
                spans[-1][1] = end
            else:
                spans.append([end - count, end])
            data = data[count:]
    finally:
        os.close(fd)
    path = os.path.join(context["directory"], ".echo-display")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write((displayed + "\n").encode(encoding, errors))
    atomic_json(os.path.join(context["directory"], ".echo.json"), {"id": context["id"], "spans": spans})


def copy_range(source, destination, count):
    """Return a saved byte range with bounded memory."""
    while count:
        data = source.read(min(count, 65536))
        if not data:
            raise OSError("saved response ended unexpectedly")
        write_all(destination, data)
        count -= len(data)


def replay(context, name, destination):
    """Return saved bytes, applying only the trailing value's display cap."""
    spans, display = [], None
    directory = context["directory"]
    try:
        with open_regular(os.path.join(directory, name)) as source:
            size = os.fstat(source.fileno()).st_size
            if name == "stdout" and os.path.exists(os.path.join(directory, ".echo.json")):
                try:
                    spans = read_meta(os.path.join(directory, ".echo.json"), context["id"])["spans"]
                    previous = 0
                    for start, end in spans:
                        if type(start) is not int or type(end) is not int or not previous <= start < end <= size:
                            raise ValueError("invalid echo range")
                        previous = end
                    display = open_regular(os.path.join(directory, ".echo-display"))
                except (OSError, ValueError, KeyError, TypeError):
                    spans = []
            for index, (start, end) in enumerate(spans):
                copy_range(source, destination, start - source.tell())
                if index == 0:
                    copy_range(display, destination, os.fstat(display.fileno()).st_size)
                source.seek(end)
            copy_range(source, destination, size - source.tell())
    finally:
        if display is not None:
            display.close()


def deliver(context):
    """Return the response BEFORE exiting; a lost caller never removes saved output."""
    errors = flush_response()
    destinations = context["destinations"]
    failed = []
    try:
        try:
            write_all(destinations[1], ("scrills: run " + context["id"] + "\n").encode("ascii"))
        except OSError:
            pass
        for name, destination in zip(STREAMS, destinations):
            try:
                replay(context, name, destination)
            except OSError:
                failed.append(name)
        try:
            path = os.path.join(context["directory"], "meta.json")
            meta = read_meta(path, context["id"])
            if errors:
                meta["complete"] = False
                meta["capture_errors"] = {**meta.get("capture_errors", {}), **errors}
            meta["delivery_failed"] = failed
            atomic_json(path, meta)
            for filename in (".echo.json", ".echo-display"):
                try:
                    os.unlink(os.path.join(context["directory"], filename))
                except FileNotFoundError:
                    pass
        except (OSError, ValueError):
            pass
    finally:
        for fd in destinations:
            os.close(fd)


def safe_text(data):
    """Render bytes as readable text without executing terminal control sequences."""
    text = data.decode("utf-8", errors="backslashreplace")
    return "".join(char if char in "\n\t" or char.isprintable() else "\\x{:02x}".format(ord(char)) for char in text)


def label(value):
    """Bound and quote arbitrary metadata in a single display line."""
    return repr(str(value)[:300])[1:-1]


def options(argv):
    """Parse a small read-only history surface; line selections retain the byte safety cap."""
    parser = argparse.ArgumentParser(prog="scrills history", description="Recover saved responses without executing code.")
    parser.add_argument("id", nargs="?")
    parser.add_argument("--offset", type=int)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--stream", choices=("both", *STREAMS), default="both")
    parser.add_argument("--line", type=int)
    parser.add_argument("--lines", type=int)
    args = parser.parse_args(argv)
    if args.id is None and argv:
        parser.error("page options require a run ID")
    if args.id is not None and not ID.fullmatch(args.id):
        parser.error("use the full 16-character hexadecimal run ID")
    if args.offset is not None and args.offset < 0:
        parser.error("--offset must be zero or greater")
    if not 1 <= args.limit <= MAX_LIMIT:
        parser.error("--limit must be between 1 and 65536 bytes")
    if args.line is not None and args.line < 1 or args.lines is not None and args.lines < 1:
        parser.error("--line and --lines must be positive")
    if args.offset is not None and (args.line is not None or args.lines is not None):
        parser.error("byte offsets and line selection cannot be combined")
    return args


def listing(root):
    """List the newest twenty independently retained responses, never guessed log entries."""
    if not os.path.lexists(root):
        print("no responses saved yet")
        return
    private_dir(root)
    entries = []
    for ident in os.listdir(root):
        if not ID.fullmatch(ident):
            continue
        try:
            directory = os.path.join(root, ident)
            private_dir(directory)
            meta = read_meta(os.path.join(directory, "meta.json"), ident)
            entries.append(meta)
        except (OSError, ValueError) as error:
            print(f"scrills history: cannot read {ident}: {error}", file=sys.stderr)
    def started(item):
        try:
            return datetime.datetime.fromisoformat(item.get("started", "")).timestamp(), item["id"]
        except (TypeError, ValueError, OverflowError, OSError):
            return float("-inf"), item["id"]

    entries.sort(key=started, reverse=True)
    if not entries:
        print("no responses saved yet")
    for meta in entries[:20]:
        state = label(meta.get("status", "not finalized")) if meta.get("complete") is True else "incomplete"
        print(f"{meta['id']}  {label(meta.get('name', '?'))}  {state}  {label(meta.get('started', '?'))}  {label(meta.get('cwd', '?'))}")


def page(handles, args):
    """Read a virtual stdout-then-stderr stream using bounded buffers and source-byte cursors."""
    start = args.offset or 0
    line_skip = (args.line or 1) - 1
    line_left = args.lines
    position = 0
    used = rendered = 0
    pieces = []
    done = False
    for name, handle, size in handles:
        if done:
            break
        if args.line is None and args.lines is None:
            skip = min(max(0, start - position), size)
            handle.seek(skip)
            position += skip
        while not done:
            chunk = handle.read(min(8192, args.limit - used))
            if not chunk:
                break
            if line_skip:
                offset = 0
                while line_skip:
                    newline = chunk.find(b"\n", offset)
                    if newline < 0:
                        position += len(chunk)
                        chunk = b""
                        break
                    line_skip -= 1
                    offset = newline + 1
                if not line_skip:
                    position += offset
                    chunk = chunk[offset:]
                if not chunk:
                    continue
            if line_left is not None:
                end = 0
                while line_left:
                    newline = chunk.find(b"\n", end)
                    if newline < 0:
                        break
                    line_left -= 1
                    end = newline + 1
                if line_left == 0:
                    chunk = chunk[:end]
                    done = True
            text = safe_text(chunk)
            # Escaping binary/control bytes must not turn an 8 KiB page into an unbounded display.
            if len(text) > args.limit - rendered:
                low, high = 0, len(chunk)
                while low < high:
                    mid = (low + high + 1) // 2
                    if len(safe_text(chunk[:mid])) <= args.limit - rendered:
                        low = mid
                    else:
                        high = mid - 1
                chunk = chunk[:max(1, low)]
                text = safe_text(chunk)
                done = True
            if chunk:
                pieces.append((name, position, position + len(chunk), text))
                position += len(chunk)
                used += len(chunk)
                rendered += len(text)
            if used == args.limit or rendered == args.limit:
                done = True
    return pieces, position, sum(size for _, _, size in handles)


def show(home, argv):
    """List or recover output without requiring the runtime, registry or original source."""
    args = options(argv)
    root = os.path.join(home, ".results")
    handles = []
    try:
        if args.id is None:
            listing(root)
            return 0
        directory = os.path.join(root, args.id)
        if not os.path.lexists(directory):
            print(f"scrills history: no saved response for {args.id}", file=sys.stderr)
            return 2
        private_dir(root)
        private_dir(directory)
        meta = read_meta(os.path.join(directory, "meta.json"), args.id)
        selected = STREAMS if args.stream == "both" else (args.stream,)
        for name in selected:
            handle = open_regular(os.path.join(directory, name))
            handles.append((name, handle, os.fstat(handle.fileno()).st_size))
        pieces, next_offset, total = page(handles, args)
        state = "complete" if meta.get("complete") is True else "incomplete (not a completed response)"
        print(f"run: {args.id}  {label(meta.get('name', '?'))}  {state}")
        print(f"execution: {label(meta.get('status', 'not finalized'))}  exit: {label(meta.get('exit', '?'))}")
        if meta.get("outcome"):
            print(f"outcome: {label(meta['outcome'])}")
        print(f"view: {args.stream}  {total} bytes" + (" (stdout then stderr, not temporal order)" if args.stream == "both" else ""))
        for name, _, size in handles:
            print(f"{name}: {size} bytes")
        if meta.get("capture_errors"):
            print("capture: incomplete; some response bytes could not be retained")
        for name, start, end, text in pieces:
            print(f"=== {name} bytes {start}:{end} ===")
            sys.stdout.write(text)
            if text and not text.endswith("\n"):
                sys.stdout.write("\n")
        print("=== end page ===")
        if next_offset < total:
            print(f"next: scrills history {args.id} --stream {args.stream} --offset {next_offset} --limit {args.limit}")
        return 0
    except (OSError, ValueError) as error:
        print(f"scrills history: cannot read response: {error}", file=sys.stderr)
        return 1
    finally:
        for _, handle, _ in handles:
            handle.close()
