"""Pi wiring: one skill symlink under the agent config directory. Pi already provides bash and
needs no permission rule; no extension, settings, environment, or model configuration is touched."""
import os
import shutil


def pi_dir():
    configured = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    return os.path.expanduser(configured) if configured else os.path.join(os.path.expanduser("~"), ".pi", "agent")


def link_path():
    return os.path.join(pi_dir(), "skills", "scrills")


def cli_path():
    cli = os.environ.get("SCRILLS_CLI", "").strip() or shutil.which("scrills")
    if not cli:
        raise RuntimeError("harness_config: cannot find the scrills command - no SCRILLS_CLI in the environment and none on PATH")
    return cli


def skill_dir():
    folder = os.path.dirname(os.path.dirname(os.path.realpath(cli_path())))
    if not os.path.isfile(os.path.join(folder, "SKILL.md")):
        raise RuntimeError(f"harness_config: no SKILL.md at {folder} - the scrills command should be a symlink into its repo")
    return folder


def link_state(target):
    path = link_path()
    if not os.path.lexists(path):
        return "missing"
    if not os.path.islink(path):
        return "not a symlink"
    return "ok" if os.path.realpath(path) == os.path.realpath(target) else "elsewhere"


def status():
    target = skill_dir()
    return {
        "harness": "pi",
        "found": os.path.isdir(pi_dir()),
        "skill_link": link_state(target),
        "link": link_path(),
        "target": target,
    }


def configured(report):
    return report["found"] and report["skill_link"] == "ok"


def apply(hook=False):
    if hook:
        raise RuntimeError("harness_config: --hook is not supported for Pi")
    target = skill_dir()
    if not os.path.isdir(pi_dir()):
        raise RuntimeError(f"harness_config: {pi_dir()} does not exist - is Pi installed?")
    changes = []
    path = link_path()
    state = link_state(target)
    if state in ("missing", "elsewhere"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.islink(path):
            os.remove(path)
        os.symlink(target, path)
        changes.append(f"{'linked' if state == 'missing' else 'relinked'} {path} -> {target}")
    elif state == "not a symlink":
        changes.append(f"skipped {path} - exists and is not a symlink, left untouched")
    return changes


def undo():
    changes = []
    path = link_path()
    if os.path.islink(path):
        try:
            ours = os.path.realpath(path) == os.path.realpath(skill_dir())
        except RuntimeError:
            ours = False
        if ours:
            os.remove(path)
            changes.append(f"removed {path}")
    return changes
