"""Save stdout/stderr, return them synchronously, and retrieve bytes by run ID.

start() redirects output; setup failure restores the caller's streams.
finish() flushes and returns the saved bytes before exit, without a helper.
show() lists recent IDs or reads one stream with a byte offset and limit.
No metadata, result interpretation, alternate echo, or completion tracking.
"""
import argparse
import fcntl
import os
import re
import stat
import sys


ID = re.compile(r"[0-9a-f]{16}\Z")
STREAMS = ("stdout", "stderr")


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


def write_all(fd, data):
    """Write all bytes, including short writes, without Python stream buffering."""
    remaining = memoryview(data)
    while remaining:
        count = os.write(fd, remaining)
        if count <= 0:
            raise OSError("short output write")
        remaining = remaining[count:]


def notice(fd, text):
    """A lost caller must not prevent returning the other stream."""
    try:
        write_all(fd, (text + "\n").encode("utf-8", errors="backslashreplace"))
    except OSError:
        pass


def private_dir(path, create=False):
    """Refuse linked/foreign directories; newly managed directories are private."""
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
    """Keep internal descriptors above stdio, even when fd 1/2 was closed."""
    copied = fcntl.fcntl(fd, fcntl.F_DUPFD, 3)
    os.set_inheritable(copied, False)
    return copied


def delivery_fd(fd):
    """Preserve the caller's destination; an originally closed stream stays undisplayed."""
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


def start(home, ident):
    """Redirect output; return descriptors survive only the CLI's exec into the boot."""
    root = os.path.join(home, ".results")
    private_dir(root, create=True)
    try:
        fd = os.open(os.path.join(root, ".gitignore"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as handle:
            handle.write("*\n")
    directory = os.path.join(root, ident)
    os.mkdir(directory, 0o700)
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
        return {"directory": directory, "id": ident, "destinations": destinations}
    except BaseException:
        for number in redirected:
            os.dup2(destinations[number - 1], number)
        for fd in destinations:
            os.close(fd)
        for name in (*STREAMS, ""):
            try:
                if name:
                    os.unlink(os.path.join(directory, name))
                else:
                    os.rmdir(directory)
            except OSError:
                pass
        raise
    finally:
        for fd in writers:
            os.close(fd)


def flush_response():
    """Flush ordinary Python and native stdio before returning the files."""
    flushed = True
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and not getattr(stream, "closed", False):
            try:
                stream.flush()
            except (OSError, ValueError, AttributeError):
                flushed = False
    try:
        import ctypes
        if ctypes.CDLL(None).fflush(None) != 0:
            flushed = False
    except (OSError, AttributeError, ImportError):
        flushed = False
    return flushed


def finish(context):
    """Return the response before exit; lost delivery never deletes saved output."""
    if context is None:
        return
    destinations = context["destinations"]
    try:
        notice(destinations[1], "scrills: run " + context["id"])
        if not flush_response():
            notice(destinations[1], "scrills: could not flush all output; saved bytes may be partial")
        for name, destination in zip(STREAMS, destinations):
            try:
                with open_regular(os.path.join(context["directory"], name)) as source:
                    remaining = os.fstat(source.fileno()).st_size
                    while remaining:
                        data = source.read(min(remaining, 65536))
                        if not data:
                            raise OSError("saved response ended unexpectedly")
                        write_all(destination, data)
                        remaining -= len(data)
            except OSError:
                notice(destinations[1], f"scrills: could not return {name}; use scrills history {context['id']} --stream {name}")
    finally:
        for fd in destinations:
            os.close(fd)


def listing(root):
    """List recent IDs by directory modification time; never delete stored responses."""
    if not os.path.lexists(root):
        print("no responses saved yet")
        return
    private_dir(root)
    entries = []
    for ident in os.listdir(root):
        if not ID.fullmatch(ident):
            continue
        try:
            info = os.stat(os.path.join(root, ident), follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(info.st_mode):
            entries.append((info.st_mtime_ns, ident))
    if not entries:
        print("no responses saved yet")
    for _, ident in sorted(entries, reverse=True)[:20]:
        print(ident)


def show(home, argv):
    """Retrieve raw bytes without exposing storage paths, importing code, or reading the registry."""
    parser = argparse.ArgumentParser(prog="scrills history", description="Read saved output by ID; offsets and limits are bytes.")
    parser.add_argument("id", nargs="?")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=8192)
    parser.add_argument("--stream", choices=STREAMS, default="stdout")
    args = parser.parse_args(argv)
    if args.id is None and argv:
        parser.error("read options require a run ID")
    if args.id is not None and not ID.fullmatch(args.id):
        parser.error("use the full 16-character hexadecimal run ID")
    if args.offset < 0 or not 1 <= args.limit <= 65536:
        parser.error("--offset must be zero or greater; --limit must be between 1 and 65536 bytes")
    root = os.path.join(home, ".results")
    try:
        if args.id is None:
            listing(root)
            return 0
        directory = os.path.join(root, args.id)
        if not os.path.lexists(directory):
            notice(2, f"scrills history: no saved output for {args.id}")
            return 2
        private_dir(root)
        private_dir(directory)
        with open_regular(os.path.join(directory, args.stream)) as source:
            source.seek(args.offset)
            data = source.read(args.limit + 1)
        write_all(1, data[:args.limit])
        if len(data) > args.limit:
            notice(2, f"next: scrills history {args.id} --stream {args.stream} --offset {args.offset + args.limit} --limit {args.limit}")
        return 0
    except (OSError, ValueError, OverflowError) as error:
        detail = error.strerror if isinstance(error, OSError) and error.strerror else str(error)
        notice(2, f"scrills history: cannot read saved output: {detail}")
        return 1
