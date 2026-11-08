"""Namespace discovery and native import parity, using only disposable local scrills."""
import pytest

from test_scrills import log_records, scrills, write_scrill


def package(root, name, body="def main():\n    print('ran')\n", version="1.0.0"):
    return write_scrill(root, name.replace(".", "/"),
                        f"---\nname: {name.rsplit('.', 1)[-1]}\ndescription: grouped\nversion: {version}\n---", body)


def test_group_discovery_is_static_and_stops_at_packages(tmp_path):
    layer, home = tmp_path / ".scrills", tmp_path / "home"
    entry = package(layer, "group1.scrill1", "raise RuntimeError('must not import')")
    package(layer, "group1.scrill1.lib.internal")
    package(layer, "group1.deep.scrill2")
    package(layer, "standalone")
    package(layer, "_hidden.secret")
    package(layer, "group1._draft")
    listing = scrills(["list"], home, tmp_path)
    assert listing.returncode == 0, listing.stderr
    assert [line for line in listing.stdout.splitlines() if line.startswith("- ")] == [
        "- group1.deep.scrill2: grouped", "- group1.scrill1: grouped", "- standalone: grouped"]
    reading = scrills(["read", "group1.scrill1"], home, tmp_path)
    assert reading.returncode == 0, reading.stderr
    assert str(entry / "README.md") in reading.stdout
    assert "=== end __init__.py ===" in reading.stdout
    assert not home.exists()
    assert scrills(["read", "group1.scrill1.lib.internal"], home, tmp_path).returncode == 2
    group = scrills(["read", "group1"], home, tmp_path)
    assert group.returncode == 2 and "group, not a scrill" in group.stderr


def test_group_layers_imports_and_qualified_registry(home, tmp_path):
    layer = tmp_path / ".scrills"
    prefix = "group_layers"
    package(home, prefix + ".same", "VALUE = 'user'")
    package(home, prefix + ".other", "VALUE = 'other'")
    package(layer, prefix + ".same", "VALUE = 'project'")
    package(layer, "other_group.tool", "VALUE = 'tool'")
    package(layer, prefix + ".caller", f"""
from .. import same, other
from scrills.other_group import tool
from .lib import helper
def main():
    print(same.VALUE, other.VALUE, tool.VALUE, helper.VALUE)
""")
    package(layer, prefix + ".caller.lib.helper", "VALUE = 'helper'")
    result = scrills(["run", prefix + ".caller"], home, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "project other tool helper\n"
    assert prefix + ".same" in result.stderr and "shadow" in result.stderr
    record = log_records(home, prefix + ".caller")[-1]
    assert record["layer"] == "project"
    assert record["imported"] == {prefix + ".caller": "1.0.0", prefix + ".same": "1.0.0",
                                  prefix + ".other": "1.0.0", "other_group.tool": "1.0.0"}
    listing = scrills(["list"], home, tmp_path)
    assert prefix + ".same  (user, shadowed by project)" in listing.stdout


@pytest.mark.parametrize("blocking_layer", ["user", "project"])
@pytest.mark.parametrize("kind", ["package", "module"])
def test_concrete_parent_blocks_namespace_without_importing(home, tmp_path, blocking_layer, kind):
    layer = tmp_path / ".scrills"
    name = "block_" + blocking_layer + "_" + kind
    blocker, namespace = (home, layer) if blocking_layer == "user" else (layer, home)
    package(namespace, name + ".child")
    if kind == "package":
        package(blocker, name, "raise RuntimeError('blocker executed')")
    else:
        blocker.mkdir(parents=True, exist_ok=True)
        (blocker / (name + ".py")).write_text("raise RuntimeError('blocker executed')\n")
    listing = scrills(["list"], home, tmp_path)
    assert listing.returncode == 0, listing.stderr
    assert f"- {name}.child:" not in listing.stdout
    assert "group blocked by" in listing.stdout
    for verb in ("read", "run"):
        result = scrills([verb, name + ".child"], home, tmp_path)
        assert result.returncode == 2, result.stderr
        assert "not a group" in result.stderr
        assert "blocker executed" not in result.stderr + result.stdout
    assert not log_records(home, name + ".child")


def test_group_missing_manual_never_falls_back(home, tmp_path):
    name = "manual_group.child"
    package(home, name)
    entry = package(tmp_path / ".scrills", name, "raise RuntimeError('executed')")
    (entry / "README.md").unlink()
    package(tmp_path / ".scrills", name + ".internal")
    result = scrills(["run", name], home, tmp_path)
    assert result.returncode == 2
    assert "README.md is required" in result.stderr
    assert "executed" not in result.stderr
    assert not log_records(home, name)
    listing = scrills(["list"], home, tmp_path)
    assert f"- {name}:" in listing.stdout
    assert f"- {name}.internal:" not in listing.stdout


def test_group_stray_winner_and_same_layer_package(home, tmp_path):
    layer = tmp_path / ".scrills"
    package(home, "stray_group.child")
    folder = layer / "stray_group"
    folder.mkdir(parents=True)
    (folder / "child.py").write_text("VALUE = 'stray'\n")
    refusal = scrills(["run", "stray_group.child"], home, tmp_path)
    assert refusal.returncode == 2 and "stray module" in refusal.stderr
    imported = scrills(["py"], home, tmp_path, stdin="from scrills.stray_group import child\nchild.VALUE")
    assert imported.stdout == "'stray'\n"
    package(layer, "stray_group.child")
    assert scrills(["run", "stray_group.child"], home, tmp_path).stdout == "ran\n"


@pytest.mark.parametrize("source", ["import scrills.hint_group.missing", "from scrills.hint_group import missing"])
def test_group_missing_hints_and_no_main(home, tmp_path, source):
    package(tmp_path / ".scrills", "hint_group.present", "VALUE = 1")
    missing = scrills(["py"], home, tmp_path, stdin=source)
    assert "no scrill named hint_group.missing" in missing.stderr
    assert "hint_group.present" in missing.stderr
    internal = scrills(["py"], home, tmp_path, stdin="import scrills.hint_group.present.missing")
    assert "no scrill named" not in internal.stderr
    no_main = scrills(["run", "hint_group.present"], home, tmp_path)
    assert no_main.returncode == 2
    assert "from scrills.hint_group import present" in no_main.stderr


def test_group_cycles_and_aliases(tmp_path):
    layer = tmp_path / ".scrills"
    package(layer, "original.child")
    (layer / "original" / "loop").symlink_to(layer, target_is_directory=True)
    (layer / "alias").symlink_to(layer / "original", target_is_directory=True)
    result = scrills(["list"], tmp_path / "home", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "- original.child: grouped" in result.stdout
    assert "- alias.child: grouped" in result.stdout
    assert "symlink cycle" in result.stdout


def test_dynamic_group_import_is_recorded(home, tmp_path):
    (tmp_path / ".scrills").mkdir()
    folder = tmp_path / ".scrills" / "dynamic_group" / "child"
    source = f"""from pathlib import Path
folder = Path({str(folder)!r})
folder.mkdir(parents=True)
(folder / 'README.md').write_text('---\\nname: child\\ndescription: dynamic\\nversion: 2.0.0\\n---\\n')
(folder / '__init__.py').write_text('VALUE = 42\\n')
from scrills.dynamic_group import child
print(child.VALUE)
"""
    result = scrills(["py"], home, tmp_path, stdin=source)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "42\n"
    assert log_records(home, "py")[-1]["imported"] == {"dynamic_group.child": "2.0.0"}


def test_group_hidden_invalid_and_keyword_components(home, tmp_path):
    layer = tmp_path / ".scrills"
    package(layer, "valid.class.child")
    package(layer, "valid._draft.child")
    package(layer, "valid.bad-name")
    result = scrills(["list"], home, tmp_path)
    assert "- valid.class.child: grouped" in result.stdout
    assert "python keyword" in result.stdout
    assert "_draft" not in result.stdout
    assert "not a valid Python identifier" in result.stdout
    for name in ("valid..child", ".valid.child", "valid/child", "valid._draft.child"):
        assert scrills(["read", name], home, tmp_path).returncode == 2
    assert scrills(["run", "valid.class.child"], home, tmp_path).returncode == 0
