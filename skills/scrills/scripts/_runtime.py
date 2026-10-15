"""Shared Scrills metadata, response history and execution runtime.

parse(), read_manual() and read_source() inspect files without importing a scrill.
version_of() samples README metadata when a run is finalized, not at import time.
start_response() captures stdout/stderr; finish_response() returns those bytes
synchronously; show_history() retrieves saved bytes without executing a scrill.
boot() consumes SCRILLS_BOOT_CTX and executes an admitted py/run call only when
this file is launched directly. Importing this module does not start a run,
register exit handlers, change streams or consume the caller's environment.
"""
import argparse
import ast
import fcntl
import io
import json
import os
import re
import stat
import sys
import tokenize
from dataclasses import dataclass, field


KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
FIELDS = ("name", "description", "version", "compatibility")


@dataclass
class Metadata:
    fields: dict = field(default_factory=dict)
    locations: dict = field(default_factory=dict)
    notices: list = field(default_factory=list)
    description: str = ""


@dataclass
class Manual:
    path: str
    text: object
    metadata: Metadata
    error: object = None


def diagnostic(origin, line, message):
    return f"{origin}:{line}: ({message})"


def scalar(value):
    """Decode one line; single-quoted YAML or JSON-style double quotes, no coercion."""
    value = value.strip()
    if value.startswith('"'):
        try:
            decoded, end = json.JSONDecoder().raw_decode(value)
        except ValueError:
            return None, "unsupported quoted value - use one line and JSON-style escapes"
        tail = value[end:]
    elif value.startswith("'"):
        pieces = []
        end = 1
        while end < len(value):
            char = value[end]
            end += 1
            if char != "'":
                pieces.append(char)
            elif end < len(value) and value[end] == "'":
                pieces.append("'")
                end += 1
            else:
                break
        else:
            return None, "unclosed quoted value"
        decoded, tail = "".join(pieces), value[end:]
    else:
        decoded = re.split(r"(?<!\S)#", value, maxsplit=1)[0].rstrip()
        tail = ""
        if decoded.startswith(("|", ">")):
            return None, "uses a YAML block scalar - scrills frontmatter takes one-line values"
        if decoded.startswith(("[", "{")) or re.match(r"^[-?](?:\s|$)", decoded):
            return None, "is a structured value - scrills frontmatter takes one-line values"
        if decoded.startswith(("!", "&", "*")):
            return None, "uses an unsupported YAML tag, anchor or alias"
    if tail.strip() and not (tail[:1].isspace() and tail.lstrip().startswith("#")):
        return None, "unexpected text after a quoted value"
    if not decoded.strip():
        return None, "is empty - supply a one-line value"
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or char in "\x7f\x85\u2028\u2029" for char in decoded):
        return None, "must be a one-line Unicode value without control characters or lone surrogates"
    return decoded, None


def meaningful(lines):
    return [(number, raw) for number, raw in lines if raw.strip() and not raw.lstrip().startswith("#")]


def blocks(lines, indent):
    """Group a key with all its continuation lines; blank lines cannot end a value."""
    groups = []
    for number, raw in lines:
        depth = len(raw) - len(raw.lstrip(" "))
        match = KEY.match(raw[indent:]) if depth == indent else None
        if match:
            groups.append((number, match.group(1), match.group(2), []))
        elif groups:
            groups[-1][3].append((number, raw))
        else:
            groups.append((number, None, "", [(number, raw)]))
    return groups


def parse(text, origin="README.md"):
    """Extract supported fields and located notices, never partial/guessed values."""
    result = Metadata()
    lines = list(enumerate(text.lstrip("\ufeff").splitlines(), 1))
    start = next((i for i, (_, raw) in enumerate(lines) if raw.strip()), None)
    if start is None or lines[start][1].rstrip() != "---":
        result.description = lines[start][1].strip() if start is not None else "(empty README.md)"
        result.notices.append(diagnostic(origin, lines[start][0] if start is not None else 1,
                                         "no frontmatter - open README.md with ---, name/description/metadata.version, ---"))
        return result
    end = next((i for i in range(start + 1, len(lines)) if lines[i][1].rstrip() == "---"), None)
    if end is None:
        result.notices.append(diagnostic(origin, lines[start][0], "frontmatter never closes - missing the second ---"))
        return result
    declared, values, invalid = {}, {}, set()

    def complain(line, message):
        result.notices.append(diagnostic(origin, line, message))

    def take(name, line, raw, continuation):
        if name in declared:
            invalid.add(name)
            complain(line, f"duplicate {name} - declare it once")
        else:
            declared[name] = line
        value, problem = scalar(raw)
        if problem:
            invalid.add(name)
            complain(line, f"{name} {problem}")
        if continuation:
            invalid.add(name)
            complain(continuation[0][0], f"{name} continues on the next line - scrills frontmatter takes one-line values")
        if name not in invalid:
            values[name] = value
        else:
            values.pop(name, None)

    metadata_line = None
    metadata_invalid = False
    for line, key, raw, children in blocks(meaningful(lines[start + 1:end]), 0):
        if key in FIELDS:
            take(key, line, raw, children)
        elif key == "metadata":
            if metadata_line is not None:
                metadata_invalid = True
                complain(line, "duplicate metadata - declare the mapping once")
            metadata_line = line
            if raw.strip() and not raw.lstrip().startswith("#"):
                metadata_invalid = True
                complain(line, "metadata must be a mapping on indented lines")
                continue
            if not children:
                continue
            indent = len(children[0][1]) - len(children[0][1].lstrip(" "))
            if indent == 0 or any(len(item) - len(item.lstrip(" ")) < indent for _, item in children):
                metadata_invalid = True
                complain(line, "metadata has inconsistent indentation")
                continue
            for child_line, child_key, child_raw, descendants in blocks(children, indent):
                if child_key == "version":
                    take("metadata.version", child_line, child_raw, descendants)
                elif child_key is None:
                    metadata_invalid = True
                    complain(child_line, "metadata must contain indented key: value entries")
        elif key is None:
            complain(line, "expected a top-level key: value - indentation uses spaces")
    if metadata_invalid:
        values.pop("metadata.version", None)
    result.fields = {key: value for key, value in values.items() if key in FIELDS}
    if "version" in declared:
        if "metadata.version" in declared:
            choice = "the top-level value wins" if "version" in values else "the top-level version is invalid; metadata.version ignored"
            complain(declared["version"], "version declared in both places - keep one; " + choice)
    elif "metadata.version" in values:
        result.fields["version"] = values["metadata.version"]
    for key in ("description", "version"):
        if key not in result.fields:
            complain(declared.get(key, lines[start][0]), f"no {key} in frontmatter")
    result.locations = declared
    result.description = result.fields.get("description", "")
    return result


def read_document(path, python=False):
    """Read a regular file (symlinks allowed), with strict decoding and no FIFO wait."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError("not a regular file")
            data = handle.read()
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0] if python else "utf-8-sig"
        text = data.decode(encoding)
        return text.replace("\r\n", "\n").replace("\r", "\n"), None
    except (OSError, UnicodeError, LookupError, SyntaxError) as error:
        return None, f"{path}: cannot read: {error}"


def read_manual(entry):
    """README is required; neither SKILL.md nor __doc__ can substitute for it."""
    path = os.path.join(os.path.dirname(entry), "README.md")
    text, error = read_document(path)
    if error is not None:
        error += "; a readable README.md is required"
        return Manual(path, None, Metadata(notices=[error], description="(README.md is required or unreadable)"), error)
    metadata = parse(text, path)
    name = os.path.basename(os.path.dirname(entry))
    stated = metadata.fields.get("name")
    if stated and stated != name:
        metadata.notices.append(diagnostic(path, metadata.locations["name"],
            f"frontmatter name '{stated}' differs from folder '{name}' - the folder name is the import name"))
    return Manual(path, text, metadata)


def read_source(entry):
    """Return the complete source even if it cannot compile; decoding failure is fatal."""
    text, error = read_document(entry, python=True)
    notices = [error] if error is not None else []
    if text is not None:
        try:
            ast.parse(text, filename=entry)
        except (SyntaxError, ValueError) as problem:
            notices.append(diagnostic(entry, getattr(problem, "lineno", None) or 1, f"doesn't parse: {problem}"))
    return text, notices, error


def version_of(module):
    """The declared README version sampled at finalization, not executed-code identity."""
    entry = getattr(module, "__file__", None)
    if not isinstance(entry, str) or os.path.basename(entry) != "__init__.py":
        return None
    return read_manual(entry).metadata.fields.get("version")


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


def start_response(home, ident):
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


def finish_response(context):
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


def list_history(root):
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


def show_history(home, argv):
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
            list_history(root)
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


def boot():
    """Execute the admitted call, recording its outcome and returning saved output."""
    RAW_CTX = os.environ.pop('SCRILLS_BOOT_CTX', None)
    if RAW_CTX is None:
        sys.stderr.write("scrills: _runtime.py is the scrills command's exec target, not a command itself - run scrills instead\n")
        sys.exit(2)
    import atexit
    import asyncio
    import importlib
    import inspect
    import time
    import traceback
    import types

    RUNTIME_FILE = os.path.realpath(__file__)
    CTX = json.loads(RAW_CTX)
    CARD = CTX['card']
    CARD_PATH = CTX['card_path']
    LOCK_FD = CTX['lock_fd']
    LOG = CTX['log']
    PREV = CTX['prev']
    LOG_LOCK = os.path.join(os.path.dirname(LOG), 'log.lock')
    STARTED = CTX['started']
    KNOWN = CTX['known']
    VERB = CTX['verb']
    BOOT_PID = os.getpid()
    if CTX['history'] is not None:
        for destination in CTX['history']['destinations']:
            os.set_inheritable(destination, False)
    OUTCOME = {}
    if VERB == 'run':
        main_module = sys.modules.get('__main__')
        if main_module is not None:
            main_module.__dict__.pop('__file__', None)
            main_module.__spec__ = None
    try:
        os.environ['TRACEPARENT'] = CTX['traceparent']
    except Exception:
        pass
    if LOCK_FD is not None:
        try:
            os.set_inheritable(LOCK_FD, False)
        except OSError:
            pass
    LAYER_DIRS = [path for path in os.environ.get('SCRILLS_LAYERS', '').split(os.pathsep) if path]
    def _plumbing(filename):
        if filename.startswith('<'):
            return filename != '<py>'
        if os.path.realpath(filename) == RUNTIME_FILE:
            return True
        if any(filename.startswith(layer + os.sep) for layer in LAYER_DIRS):
            return False
        return (os.sep + 'asyncio' + os.sep) in filename or (os.sep + 'importlib' + os.sep + '__init__.py') in filename
    def _explain_import_error(error):
        try:
            missing = getattr(error, 'name', None) or ''
            if isinstance(error, ModuleNotFoundError):
                if missing.startswith('scrills.'):
                    parts = missing.split('.')
                    if len(parts) > 1 and parts[1] and parts[1] not in KNOWN:
                        return 'scrills: no scrill named ' + parts[1] + ' (available: ' + (', '.join(KNOWN) or 'none yet') + ')'
                    return None
                if missing and not missing.startswith('.'):
                    top = missing.split('.')[0]
                    if top != 'scrills' and top not in KNOWN:
                        return "scrills: no module named '" + missing + "' - if it is a third-party package, install it with: scrills install " + top
                return None
            if isinstance(error, ImportError) and missing == 'scrills':
                text = str(error)
                marker = "cannot import name '"
                if marker not in text:
                    return None
                name = text.split(marker, 1)[1].split("'")[0]
                if name in KNOWN:
                    return None
                return 'scrills: no scrill named ' + name + ' (available: ' + (', '.join(KNOWN) or 'none yet') + ')'
        except Exception:
            pass
        return None
    def _note_error(error):
        try:
            OUTCOME['error_type'] = type(error).__name__
            layer_dirs = LAYER_DIRS
            def ours(filename):
                return filename == '<py>' or any(filename.startswith(layer + os.sep) for layer in layer_dirs)
            spot = None
            if isinstance(error, SyntaxError) and isinstance(error.filename, str) and error.lineno:
                if ours(error.filename):
                    spot = (error.filename, error.lineno)
            if spot is None:
                last = scoped = None
                tb = error.__traceback__
                while tb is not None:
                    filename = tb.tb_frame.f_code.co_filename
                    if ours(filename):
                        scoped = (filename, tb.tb_lineno)
                    elif not filename.startswith('<') and os.path.realpath(filename) != RUNTIME_FILE:
                        last = (filename, tb.tb_lineno)
                    tb = tb.tb_next
                spot = scoped or last
            if spot is not None:
                OUTCOME['error_at'] = spot[0] + ':' + str(spot[1])
        except Exception:
            pass
    def _write_record():
        if os.getpid() != BOOT_PID:
            return
        try:
            if CARD is None or 'exit' not in OUTCOME:
                return
            record = dict(CARD)
            code = OUTCOME['exit']
            if code == 0:
                status = 'ok'
            elif OUTCOME.get('outcome'):
                status = 'outcome'
            else:
                status = 'error'
            record.update(exit=code, status=status, ms=int((time.time() - STARTED) * 1000))
            if status == 'outcome':
                record['outcome'] = OUTCOME['outcome']
            for key in ('error_type', 'error_at'):
                if key in OUTCOME:
                    record[key] = OUTCOME[key]
            try:
                names = sorted(set(name.split('.')[1] for name in sys.modules if name.startswith('scrills.')))
                if names:
                    record['imported'] = dict((name, version_of(sys.modules.get('scrills.' + name))) for name in names)
            except Exception:
                pass
            lock = None
            try:
                lock = os.open(LOG_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if lock is not None:
                    os.close(lock)
                lock = None
            try:
                handle = open(LOG, 'a', encoding='utf-8')
                try:
                    if lock is not None and os.path.getsize(LOG) > 1048576:
                        os.replace(LOG, PREV)
                        handle.close()
                        handle = open(LOG, 'a', encoding='utf-8')
                    handle.write(json.dumps(record) + '\n')
                finally:
                    handle.close()
            finally:
                if lock is not None:
                    os.close(lock)
        except Exception:
            pass
        finally:
            if CARD is not None and 'exit' in OUTCOME:
                try:
                    os.remove(CARD_PATH)
                except OSError:
                    pass
            try:
                finish_response(CTX['history'])
            except Exception:
                pass
    def _finish(code):
        OUTCOME['exit'] = code
        sys.exit(code)
    atexit.register(_write_record)

    if VERB == 'py':
        code = sys.stdin.read()
        if not code.strip():
            print("scrills py: no code on stdin. Pass it as a heredoc:\n  scrills py <<'PY'\n  ...\n  PY", file=sys.stderr)
            _finish(2)
        try:
            ECHO_CAP = int(os.environ.get('SCRILLS_ECHO_CAP', '') or 8192)
        except ValueError:
            ECHO_CAP = 8192
        sys.modules['_scrills_runtime'] = sys.modules[__name__]
        mod = types.ModuleType('__main__')
        sys.modules['__main__'] = mod
        ns = mod.__dict__
        loop = None
        def _run(codeobj):
            nonlocal loop
            value = eval(codeobj, ns)
            if codeobj.co_flags & inspect.CO_COROUTINE:
                if loop is None:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                return loop.run_until_complete(value)
            return value
        def _echo(value):
            text = repr(value)
            if ECHO_CAP > 0 and len(text) > ECHO_CAP:
                text = text[:ECHO_CAP] + '\n… (+{:,} chars — print it or write it to a file)'.format(len(text) - ECHO_CAP)
            print(text)
        status = 0
        flag = ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
        try:
            tree = compile(code, '<py>', 'exec', ast.PyCF_ONLY_AST | flag)
            trailing = None
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                trailing = ast.Expression(tree.body.pop().value)
            if tree.body:
                _run(compile(tree, '<py>', 'exec', flag))
            if trailing is not None:
                value = _run(compile(trailing, '<py>', 'eval', flag))
                if value is not None:
                    sys.stdout.flush()
                    _echo(value)
        except SystemExit as stop:
            if stop.code is None:
                status = 0
            elif isinstance(stop.code, int):
                status = stop.code & 0xFF
            else:
                print(stop.code, file=sys.stderr)
                status = 1
        except BaseException as error:
            if isinstance(error, KeyboardInterrupt):
                raise
            _note_error(error)
            tb = error.__traceback__
            while tb is not None and _plumbing(tb.tb_frame.f_code.co_filename):
                tb = tb.tb_next
            sys.stdout.flush()
            sys.stderr.write(''.join(traceback.format_exception(type(error), error, tb)))
            hint = _explain_import_error(error)
            if hint:
                sys.stderr.write(hint + '\n')
            status = 1
        finally:
            if loop is not None:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    loop.run_until_complete(loop.shutdown_default_executor())
                except BaseException:
                    pass
                loop.close()
        _finish(status)

    else:
        name = sys.argv.pop(1)
        code = 1
        chosen = False
        module = None
        try:
            module = importlib.import_module('scrills.' + name)
            entry = getattr(module, 'main', None)
            if callable(entry):
                sys.argv[0] = name
                value = entry()
                if inspect.iscoroutine(value):
                    value = asyncio.run(value)
                if value is None:
                    code = 0
                elif isinstance(value, int):
                    code = value & 0xFF
                    chosen = True
                else:
                    print(value, file=sys.stderr)
                    code = 1
            else:
                print('scrills run: ' + name + ' defines no main(), so it only imports. Use it from py: from scrills import ' + name, file=sys.stderr)
                code = 2
        except SystemExit as stop:
            if stop.code is None:
                code = 0
            elif isinstance(stop.code, int):
                code = stop.code & 0xFF
                chosen = module is not None
            else:
                print(stop.code, file=sys.stderr)
                code = 1
        except BaseException as error:
            if isinstance(error, KeyboardInterrupt):
                raise
            _note_error(error)
            tb = error.__traceback__
            while tb is not None and _plumbing(tb.tb_frame.f_code.co_filename):
                tb = tb.tb_next
            sys.stdout.flush()
            sys.stderr.write(''.join(traceback.format_exception(type(error), error, tb)))
            hint = _explain_import_error(error)
            if hint:
                sys.stderr.write(hint + '\n')
            code = 1
        if chosen and code != 0:
            try:
                table = getattr(module, 'EXITS', None)
                named = table.get(code) if isinstance(table, dict) else None
                if isinstance(named, str) and named.strip():
                    OUTCOME['outcome'] = named.strip()
            except Exception:
                pass
        _finish(code)


if __name__ == "__main__":
    boot()
