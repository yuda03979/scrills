# scrills resolver (installed copy - the source is scripts/_scrills_pth.py in the repo; the CLI
# rewrites this file atomically when the source changes).
# install() runs at every interpreter start in this venv, called by scrills.pth (a .pth import
# line, processed through Python 3.17) or scrills.start (PEP 829, understood from 3.15 - the
# same venv keeps working across that transition). It synthesizes the `scrills` package over the
# layer directories in $SCRILLS_LAYERS (else the user library), so anything this python starts -
# snippets, scrills, worker processes - can `from scrills import <name>`; and when the
# interpreter got no pycache redirect of its own (spawned workers inherit -I but not -X), it
# points bytecode caches at <home>/.pycache so committed .scrills folders stay clean.
import importlib.machinery
import os
import sys
import types


def _home():
    raw = os.environ.get("SCRILLS_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".scrills")
    return os.path.abspath(os.path.expanduser(raw))


def _layers():
    raw = os.environ.get("SCRILLS_LAYERS", "")
    paths = [path for path in raw.split(os.pathsep) if path]
    return paths or [_home()]


def install():
    if sys.pycache_prefix is None:
        sys.pycache_prefix = os.path.join(_home(), ".pycache")
    if "scrills" in sys.modules:
        return
    pkg = types.ModuleType("scrills")
    pkg.__path__ = _layers()
    pkg.__spec__ = importlib.machinery.ModuleSpec("scrills", None, is_package=True)
    pkg.__spec__.submodule_search_locations = pkg.__path__
    sys.modules["scrills"] = pkg
