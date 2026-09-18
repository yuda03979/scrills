"""Claude Code wiring: the ~/.claude/skills/scrills symlink and one permissions.allow rule.
Nothing else - no hooks, no env vars, no model settings. settings.json is merged, never
replaced: other keys pass through untouched; a file that does not parse is refused."""
import json
import os
import shutil

RULE = "Bash(scrills:*)"


def claude_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")


def link_path():
    return os.path.join(claude_dir(), "skills", "scrills")


def settings_path():
    return os.path.join(claude_dir(), "settings.json")


def skill_dir():
    cli = os.environ.get("SCRILLS_CLI", "").strip() or shutil.which("scrills")
    if not cli:
        raise RuntimeError("harness_config: cannot find the scrills command - no SCRILLS_CLI in the environment and none on PATH")
    folder = os.path.dirname(os.path.dirname(os.path.realpath(cli)))
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


def load_settings():
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        settings = json.loads(text)
    except ValueError:
        raise RuntimeError(f"harness_config: {path} is not valid JSON - fix it by hand, nothing was touched") from None
    if not isinstance(settings, dict):
        raise RuntimeError(f"harness_config: {path} does not hold an object - fix it by hand, nothing was touched")
    return settings


def write_settings(settings):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def allow_list(settings):
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        raise RuntimeError(f"harness_config: permissions in {settings_path()} is not an object - fix it by hand, nothing was touched")
    allow = permissions.setdefault("allow", [])
    if not isinstance(allow, list):
        raise RuntimeError(f"harness_config: permissions.allow in {settings_path()} is not a list - fix it by hand, nothing was touched")
    return allow


def status():
    target = skill_dir()
    settings = load_settings()
    permissions = settings.get("permissions")
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    return {
        "harness": "claude",
        "found": os.path.isdir(claude_dir()),
        "skill_link": link_state(target),
        "permission": "present" if isinstance(allow, list) and RULE in allow else "absent",
        "link": link_path(),
        "settings": settings_path(),
        "target": target,
    }


def configured(report):
    return report["found"] and report["skill_link"] == "ok" and report["permission"] == "present"


def apply():
    target = skill_dir()
    if not os.path.isdir(claude_dir()):
        raise RuntimeError(f"harness_config: {claude_dir()} does not exist - is Claude Code installed?")
    settings = load_settings()
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
    allow = allow_list(settings)
    if RULE not in allow:
        allow.append(RULE)
        write_settings(settings)
        changes.append(f"allowed {RULE} in {settings_path()}")
    return changes


def undo():
    settings = load_settings()
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
    permissions = settings.get("permissions")
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    if isinstance(allow, list) and RULE in allow:
        while RULE in allow:
            allow.remove(RULE)
        write_settings(settings)
        changes.append(f"removed {RULE} from {settings_path()}")
    return changes
