"""Claude Code wiring: the ~/.claude/skills/scrills symlink, one permissions.allow rule, and -
opt-in - one SessionStart hook that injects the library listing into every session. Nothing
else: no env vars, no model settings. settings.json is merged, never replaced: other keys pass
through untouched; a file that does not parse is refused."""
import json
import os
import shutil
import sys

RULE = "Bash(scrills:*)"
HOOK_MARKER = "SCRILLS_HOOK="


def claude_dir():
    return os.path.join(os.path.expanduser("~"), ".claude")


def link_path():
    return os.path.join(claude_dir(), "skills", "scrills")


def settings_path():
    return os.path.join(claude_dir(), "settings.json")


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


def own_version():
    module = sys.modules.get("scrills.harness_config")
    lines = ((getattr(module, "__doc__", None) or "").strip().splitlines() + ["---"])[1:]
    for line in lines[: lines.index("---")]:
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return "0"


def cli_path():
    cli = os.environ.get("SCRILLS_CLI", "").strip() or shutil.which("scrills")
    if not cli:
        raise RuntimeError("harness_config: cannot find the scrills command - no SCRILLS_CLI in the environment and none on PATH")
    return cli


def hook_command():
    return (
        f"{HOOK_MARKER}{own_version()} "
        "echo 'the scrills library - check it before writing logic; use from bash: scrills py' "
        f"&& {cli_path()} list 2>/dev/null"
    )


def hook_entries(settings):
    hooks = settings.get("hooks")
    starts = hooks.get("SessionStart") if isinstance(hooks, dict) else None
    return starts if isinstance(starts, list) else []


def is_our_hook(entry):
    if not isinstance(entry, dict):
        return False
    inner = entry.get("hooks")
    if not isinstance(inner, list):
        return False
    return any(isinstance(item, dict) and str(item.get("command", "")).startswith(HOOK_MARKER) for item in inner)


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
        "hook": "present" if any(is_our_hook(entry) for entry in hook_entries(settings)) else "absent",
        "link": link_path(),
        "settings": settings_path(),
        "target": target,
    }


def configured(report):
    return report["found"] and report["skill_link"] == "ok" and report["permission"] == "present"


def apply(hook=False):
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
    written = []
    allow = allow_list(settings)
    if RULE not in allow:
        allow.append(RULE)
        written.append(f"allowed {RULE} in {settings_path()}")
    if hook:
        hooks = settings.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise RuntimeError(f"harness_config: hooks in {settings_path()} is not an object - fix it by hand, nothing was touched")
        starts = hooks.setdefault("SessionStart", [])
        if not isinstance(starts, list):
            raise RuntimeError(f"harness_config: hooks.SessionStart in {settings_path()} is not a list - fix it by hand, nothing was touched")
        if not any(is_our_hook(entry) for entry in starts):
            starts.append({"hooks": [{"type": "command", "command": hook_command(), "timeout": 10}]})
            written.append(f"hooked SessionStart in {settings_path()} - injects the library listing into every session")
    if written:
        write_settings(settings)
        changes.extend(written)
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
    written = []
    permissions = settings.get("permissions")
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    if isinstance(allow, list) and RULE in allow:
        while RULE in allow:
            allow.remove(RULE)
        written.append(f"removed {RULE} from {settings_path()}")
    starts = hook_entries(settings)
    kept = [entry for entry in starts if not is_our_hook(entry)]
    if len(kept) != len(starts):
        settings["hooks"]["SessionStart"] = kept
        written.append(f"removed the SessionStart hook from {settings_path()}")
    if written:
        write_settings(settings)
        changes.extend(written)
    return changes
