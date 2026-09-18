# Drives the shipped examples/.scrills layer through the real CLI. Offline: harness_config runs
# against a scratch HOME with a fake ~/.claude, so no real settings are ever touched - never let
# a test spend money or make noise.
# Run: uv run --with pytest==8.4.2 python -m pytest tests/ -q
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import base_env

CLI = str(Path(__file__).resolve().parent.parent / "scrills" / "scripts" / "scrills")
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SKILL_DIR = Path(__file__).resolve().parent.parent / "scrills"


def scrills(args, home, stdin=None, extra_env=None, cwd=EXAMPLES):
    return subprocess.run(
        [sys.executable, CLI, *args],
        input=stdin,
        env={**base_env(), "SCRILLS_HOME": str(home), **(extra_env or {})},
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def test_list_shows_the_example_layer(home):
    result = scrills(["list"], home)
    assert result.returncode == 0
    assert "harness_config  (project)" in result.stdout
    assert "runs standalone: scrills run harness_config" in result.stdout
    assert "(no " not in result.stdout
    assert "differs from folder" not in result.stdout


def claude_home(tmp_path, settings=None):
    fake = tmp_path / "fakehome"
    (fake / ".claude" / "skills").mkdir(parents=True)
    if settings is not None:
        (fake / ".claude" / "settings.json").write_text(settings)
    return fake, {"HOME": str(fake)}


def settings_of(fake):
    return json.loads((fake / ".claude" / "settings.json").read_text())


def harness_records(home):
    path = Path(home) / ".runs" / "log.jsonl"
    lines = path.read_text().splitlines() if path.exists() else []
    return [json.loads(line) for line in lines if '"harness_config"' in line]


def test_harness_config_apply_links_and_allows_preserving_settings(home, tmp_path):
    seeded = {"model": "opus", "permissions": {"allow": ["Bash(git status)"], "defaultMode": "default"}, "hooks": {"Stop": []}}
    fake, env = claude_home(tmp_path, json.dumps(seeded))
    result = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    link = fake / ".claude" / "skills" / "scrills"
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(SKILL_DIR)
    data = settings_of(fake)
    assert data["model"] == "opus"
    assert data["hooks"] == {"Stop": []}
    assert data["permissions"]["defaultMode"] == "default"
    assert data["permissions"]["allow"] == ["Bash(git status)", "Bash(scrills:*)"]
    before = (fake / ".claude" / "settings.json").read_bytes()
    again = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert again.returncode == 0
    assert "nothing to change" in again.stdout
    assert (fake / ".claude" / "settings.json").read_bytes() == before


def test_harness_config_status_reports_and_names_its_exit(home, tmp_path):
    _, env = claude_home(tmp_path)
    result = scrills(["run", "harness_config"], home, extra_env=env)
    assert result.returncode == 1
    assert "skill_link: missing" in result.stdout
    assert "permission: absent" in result.stdout
    record = harness_records(home)[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "not configured"
    assert scrills(["run", "harness_config", "apply"], home, extra_env=env).returncode == 0
    done = scrills(["run", "harness_config", "status"], home, extra_env=env)
    assert done.returncode == 0
    assert "skill_link: ok" in done.stdout
    assert "permission: present" in done.stdout


def test_harness_config_undo_removes_only_ours(home, tmp_path):
    seeded = {"permissions": {"allow": ["Bash(git status)"]}, "env": {"KEEP": "1"}}
    fake, env = claude_home(tmp_path, json.dumps(seeded))
    assert scrills(["run", "harness_config", "apply"], home, extra_env=env).returncode == 0
    result = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (fake / ".claude" / "skills" / "scrills").exists()
    data = settings_of(fake)
    assert data["permissions"]["allow"] == ["Bash(git status)"]
    assert data["env"] == {"KEEP": "1"}
    again = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert again.returncode == 0
    assert "nothing to change" in again.stdout


def test_harness_config_never_touches_a_real_folder(home, tmp_path):
    fake, env = claude_home(tmp_path)
    occupied = fake / ".claude" / "skills" / "scrills"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("mine")
    result = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert result.returncode == 1
    assert "not a symlink" in result.stdout
    assert (occupied / "keep.txt").read_text() == "mine"
    assert settings_of(fake)["permissions"]["allow"] == ["Bash(scrills:*)"]
    undone = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert undone.returncode == 0
    assert (occupied / "keep.txt").read_text() == "mine"


def test_harness_config_refuses_corrupt_settings_before_touching_anything(home, tmp_path):
    fake, env = claude_home(tmp_path, "{broken json")
    result = scrills(["run", "harness_config", "apply"], home, extra_env=env)
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr
    assert (fake / ".claude" / "settings.json").read_text() == "{broken json"
    assert not (fake / ".claude" / "skills" / "scrills").exists()


def test_harness_config_usage(home, tmp_path):
    _, env = claude_home(tmp_path)
    result = scrills(["run", "harness_config", "configure"], home, extra_env=env)
    assert result.returncode == 2
    assert "usage: scrills run harness_config" in result.stderr
    record = harness_records(home)[-1]
    assert record["status"] == "outcome"
    assert record["outcome"] == "usage"


BUS_HOOK = {"hooks": [{"type": "command", "command": "BUS_HARNESS=0.1.1 /usr/bin/true", "timeout": 10}]}


def session_start(fake):
    return settings_of(fake).get("hooks", {}).get("SessionStart", [])


def test_harness_config_hook_merges_beside_foreign_hooks(home, tmp_path):
    seeded = {"hooks": {"SessionStart": [BUS_HOOK], "Stop": [{"hooks": []}]}, "model": "opus"}
    fake, env = claude_home(tmp_path, json.dumps(seeded))
    result = scrills(["run", "harness_config", "apply", "--hook"], home, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    entries = session_start(fake)
    assert entries[0] == BUS_HOOK
    assert len(entries) == 2
    command = entries[1]["hooks"][0]["command"]
    assert command.startswith("SCRILLS_HOOK=")
    assert " list" in command
    assert settings_of(fake)["hooks"]["Stop"] == [{"hooks": []}]
    assert settings_of(fake)["model"] == "opus"
    before = (fake / ".claude" / "settings.json").read_bytes()
    again = scrills(["run", "harness_config", "apply", "--hook"], home, extra_env=env)
    assert again.returncode == 0
    assert "nothing to change" in again.stdout
    assert (fake / ".claude" / "settings.json").read_bytes() == before


def test_harness_config_plain_apply_adds_no_hook_and_status_reports(home, tmp_path):
    fake, env = claude_home(tmp_path)
    assert scrills(["run", "harness_config", "apply"], home, extra_env=env).returncode == 0
    assert session_start(fake) == []
    status = scrills(["run", "harness_config", "status"], home, extra_env=env)
    assert status.returncode == 0
    assert "hook: absent" in status.stdout
    assert scrills(["run", "harness_config", "apply", "--hook"], home, extra_env=env).returncode == 0
    status = scrills(["run", "harness_config", "status"], home, extra_env=env)
    assert status.returncode == 0
    assert "hook: present" in status.stdout


def test_harness_config_undo_removes_only_our_hook(home, tmp_path):
    seeded = {"hooks": {"SessionStart": [BUS_HOOK]}}
    fake, env = claude_home(tmp_path, json.dumps(seeded))
    assert scrills(["run", "harness_config", "apply", "--hook"], home, extra_env=env).returncode == 0
    assert len(session_start(fake)) == 2
    result = scrills(["run", "harness_config", "undo"], home, extra_env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert session_start(fake) == [BUS_HOOK]
