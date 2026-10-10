# scrills boot - the scrills command's exec target, never a command itself. Every py/run child is
# this file under the venv python: the resolver (site-packages/_scrills_pth.py + scrills.pth) has
# already synthesized the `scrills` package, the run context arrives as JSON in SCRILLS_BOOT_CTX
# (popped immediately, so nothing the run spawns ever sees it), and a py snippet arrives on stdin.
# For run it erases this file's __main__ identity before user code, so multiprocessing never
# selects _boot.py as a spawn/forkserver init_main_from_path. Everything here is plumbing:
# tracebacks are trimmed of these frames and error_at never points into this file.
import ast, atexit, asyncio, importlib, inspect, json, os, sys, time, traceback, types
BOOT_FILE = os.path.realpath(__file__)
RAW_CTX = os.environ.pop('SCRILLS_BOOT_CTX', None)
if RAW_CTX is None:
    sys.stderr.write("scrills: _boot.py is the scrills command's exec target, not a command itself - run scrills instead\n")
    sys.exit(2)
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
    if os.path.realpath(filename) == BOOT_FILE:
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
                elif not filename.startswith('<') and os.path.realpath(filename) != BOOT_FILE:
                    last = (filename, tb.tb_lineno)
                tb = tb.tb_next
            spot = scoped or last
        if spot is not None:
            OUTCOME['error_at'] = spot[0] + ':' + str(spot[1])
    except Exception:
        pass
def _fm_value(value):
    if not value or value[0] in ('[', '{'):
        return None
    if value[0] in ('|', '>'):
        rest = value[1:]
        if rest[:1] in ('+', '-'):
            rest = rest[1:]
        if rest == '' or rest.isdigit():
            return None
    if len(value) >= 2 and value[0] == value[-1] and value[:1] in ('"', "'"):
        value = value[1:-1]
    return value or None
def _fm_version(text):
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != '---':
        return None
    closed = False
    top = nested = None
    in_metadata = False
    metadata_indent = None
    for raw in lines[1:]:
        line = raw.strip()
        if line == '---':
            closed = True
            break
        if not line:
            continue
        if raw[:1] not in (' ', '\t'):
            in_metadata = line == 'metadata:'
            if line.startswith('version:') and top is None:
                top = _fm_value(line[len('version:'):].strip())
            continue
        if in_metadata:
            depth = len(raw) - len(raw.lstrip())
            if metadata_indent is None:
                metadata_indent = depth
            if depth == metadata_indent and line.startswith('version:') and nested is None:
                nested = _fm_value(line[len('version:'):].strip())
    if not closed:
        return None
    return top if top is not None else nested
def _version_of(module):
    entry = getattr(module, '__file__', None)
    if entry:
        try:
            with open(os.path.join(os.path.dirname(entry), 'SKILL.md'), encoding='utf-8', errors='replace') as handle:
                return _fm_version(handle.read())
        except OSError:
            pass
    return _fm_version(inspect.cleandoc(getattr(module, '__doc__', None) or ''))
def _write_record():
    if CARD is None or 'exit' not in OUTCOME or os.getpid() != BOOT_PID:
        return
    try:
        import fcntl
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
        names = sorted(set(name.split('.')[1] for name in sys.modules if name.startswith('scrills.')))
        if names:
            record['imported'] = dict((name, _version_of(sys.modules.get('scrills.' + name))) for name in names)
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
        try:
            os.remove(CARD_PATH)
        except OSError:
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
    sys.modules['_scrills_boot'] = sys.modules['__main__']
    mod = types.ModuleType('__main__')
    sys.modules['__main__'] = mod
    ns = mod.__dict__
    loop = None
    def _run(codeobj):
        global loop
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
