# Manifest/reading contract: drive list, read and the registry against the same inputs.
# All fixtures are local and inert; read must not import even the deliberately hostile entry.
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import base_env
from test_scrills import CLI, entry_chunk, log_records, scrills


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".scrills").mkdir()
    return tmp_path


def manual(body, name="probe"):
    description = "" if re.search(r"^description:", body, re.M) else "description: parser probe\n"
    return f"---\nname: {name}\n{description}{body}\n---\n"


def make_scrill(root, name="probe", text=None, location="README.md", source=None):
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    source = source or '"""Entry instructions: main() is inert."""\ndef main():\n    return 0\n'
    if location == "docstring":
        source = repr(text) + "\ndef main():\n    return 0\n"
    elif text is not None:
        (folder / location).write_text(text, encoding="utf-8")
    (folder / "__init__.py").write_text(source, encoding="utf-8")
    return folder


CASES = [
    pytest.param("version: 1.0.0\n  continued", "parser probe", None, "continues", id="top-continuation"),
    pytest.param("metadata: # release\n  version: 2.0.0", "parser probe", "2.0.0", None, id="metadata-comment"),
    pytest.param("description: first\n\n  second\nversion: 1.0.0", "", "1.0.0", "continues", id="blank-continuation"),
    pytest.param("metadata:\n  version: 1.0.0\n\n    continued", "parser probe", None, "continues", id="nested-continuation"),
    pytest.param("version: >2-", "parser probe", None, "block scalar", id="block-indent-chomp"),
    pytest.param("version: > # folded", "parser probe", None, "block scalar", id="block-comment"),
    pytest.param('version: ""\nmetadata:\n  version: 2.0.0', "parser probe", None, "metadata.version ignored", id="empty-top"),
    pytest.param("version: 1.0.0\nversion: 2.0.0", "parser probe", None, "duplicate version", id="duplicate-top"),
    pytest.param("metadata:\n  version: 1.0.0\n  version: 2.0.0", "parser probe", None, "duplicate metadata.version", id="duplicate-nested"),
    pytest.param("description: first\n  # just a comment\nversion: 1.0.0", "first", "1.0.0", None, id="indented-comment"),
    pytest.param("metadata:\n    # deeper comment\n  version: 2.0.0", "parser probe", "2.0.0", None, id="comment-indentation"),
    pytest.param("version: 1.0.0 # release", "parser probe", "1.0.0", None, id="inline-comment"),
    pytest.param("metadata: release\n  version: 2.0.0", "parser probe", None, "metadata must be a mapping", id="metadata-scalar"),
    pytest.param("metadata:\n  version: >\n    1.0.0", "parser probe", None, "block scalar", id="nested-block"),
    pytest.param("metadata:\n  other:\n    version: 3.0.0", "parser probe", None, "no version", id="deep-version"),
    pytest.param("metadata:\n  version: 1.0.0\nmetadata:\n  version: 2.0.0", "parser probe", None, "duplicate metadata", id="duplicate-metadata"),
    pytest.param('description: "quoted # text" # comment\nversion: "1.0.0" # release', "quoted # text", "1.0.0", None, id="quoted-comments"),
    pytest.param("description: 'don''t guess'\nversion: 1.0.0", "don't guess", "1.0.0", None, id="single-quote-escape"),
    pytest.param('description: "unterminated\nversion: 1.0.0', "", "1.0.0", "quoted", id="unclosed-quote"),
    pytest.param('description: "first\\nsecond"\nversion: 1.0.0', "", "1.0.0", "one-line", id="escaped-newline"),
    pytest.param('description: "literal > and #"\nversion: 1.0.0', "literal > and #", "1.0.0", None, id="quoted-indicators"),
    pytest.param("version: 1.0.0\nmetadata:\n  version: 2.0.0", "parser probe", "1.0.0", "top-level value wins", id="top-precedence"),
]


@pytest.mark.parametrize("body,description,version,notice", CASES)
def test_manifest_rules_agree_in_listing_reading_and_registry(home, project, body, description, version, notice):
    folder = make_scrill(project / ".scrills", text=manual(body))
    listing = scrills(["list"], home, project)
    assert listing.returncode == 0, listing.stderr
    entry = f"- probe: {description}".rstrip()
    assert entry in listing.stdout.splitlines(), listing.stdout
    chunk = entry_chunk(listing.stdout, entry)
    if notice:
        assert notice in chunk
        assert str(folder) in chunk
    else:
        assert "no version" not in chunk
        assert "continues" not in chunk
    if version is None:
        assert "no version in frontmatter" in chunk
    result = scrills(["run", "probe"], home, project)
    assert result.returncode == 0, result.stderr
    assert log_records(home, "probe")[-1]["imported"] == {"probe": version}
    reading = scrills(["read", "probe"], home, project)
    assert reading.returncode == 0, reading.stderr
    if notice:
        assert notice in reading.stderr
    assert "=== end __init__.py ===" in reading.stdout


@pytest.mark.parametrize("text", ["", "A prose README\n", "---\nversion: 9.0.0\n"])
def test_readme_presence_never_falls_back(home, project, text):
    folder = make_scrill(project / ".scrills", text=text)
    (folder / "SKILL.md").write_text(manual("version: 2.0.0"))
    (folder / "__init__.py").write_text(repr(manual("version: 3.0.0")) + "\ndef main():\n    return 0\n")
    listing = scrills(["list"], home, project)
    assert listing.returncode == 0
    assert "- probe: parser probe" not in listing.stdout
    assert str(folder / "README.md") in listing.stdout
    result = scrills(["run", "probe"], home, project)
    assert result.returncode == 0, result.stderr
    assert log_records(home, "probe")[-1]["imported"] == {"probe": None}
    reading = scrills(["read", "probe"], home, project)
    assert reading.returncode == 0, reading.stderr
    assert "=== README.md" in reading.stdout
    assert "=== SKILL.md" not in reading.stdout


@pytest.mark.parametrize("shape", ["directory", "dangling", "invalid-utf8", "fifo"])
def test_selected_unreadable_manual_is_not_absent(home, project, shape):
    folder = make_scrill(project / ".scrills", text=manual("version: 3.0.0"), location="docstring")
    path = folder / "README.md"
    if shape == "directory":
        path.mkdir()
    elif shape == "dangling":
        path.symlink_to(folder / "missing.md")
    elif shape == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"\xff")
    (folder / "SKILL.md").write_text(manual("version: 2.0.0"))
    listing = scrills(["list"], home, project)
    assert listing.returncode == 0
    assert "cannot read" in listing.stdout and str(path) in listing.stdout
    assert "- probe: parser probe" not in listing.stdout
    result = scrills(["run", "probe"], home, project)
    assert result.returncode == 2, result.stderr
    assert not [r for r in log_records(home, "probe") if r.get("cwd") == str(project)]
    imported = scrills(["py"], home, project, stdin="from scrills import probe")
    assert imported.returncode == 0, imported.stderr
    assert log_records(home, "py")[-1]["imported"] == {"probe": None}
    reading = scrills(["read", "probe"], home, project)
    assert reading.returncode == 1
    assert "cannot read" in reading.stderr
    assert "=== end __init__.py ===" in reading.stdout
    assert "=== end README.md ===" not in reading.stdout


def test_read_does_not_import_or_create_a_runtime(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    body = '"""Entry instructions are here."""\nfrom pathlib import Path\nPath("executed").touch()\nraise RuntimeError("must not import")\n'
    folder = make_scrill(project / ".scrills", text=manual("version: 1.0.0") + "Human instructions.\n", source=body)
    result = scrills(["read", "probe"], home, project)
    assert result.returncode == 0, result.stderr
    assert str(folder) in result.stdout and "layer: project" in result.stdout
    assert "Human instructions." in result.stdout
    assert body in result.stdout
    assert "=== end README.md ===" in result.stdout
    assert "=== end __init__.py ===" in result.stdout
    assert not (project / "executed").exists()
    assert not home.exists(), "read is not execution or environment setup"


def test_read_has_no_internal_truncation(tmp_path):
    project = tmp_path / "project"
    text = manual("version: 1.0.0") + "Long manual line.\n" * 6000 + "MANUAL-LAST\n"
    source = '"""Entry instructions."""\n' + "# Long source line.\n" * 6000 + "VALUE = 'SOURCE-LAST'\n"
    make_scrill(project / ".scrills", text=text, source=source)
    result = scrills(["read", "probe"], tmp_path / "home", project)
    assert result.returncode == 0, result.stderr
    assert text in result.stdout and source in result.stdout
    assert f"{len(text.splitlines())} lines" in result.stdout
    assert f"{len(source.splitlines())} lines" in result.stdout
    assert result.stdout.rstrip().endswith("=== end __init__.py ===")


def test_read_uses_the_winning_provider_and_never_a_shadowed_manual(home, project):
    make_scrill(home, text=manual("version: 1.0.0") + "USER-MANUAL\n")
    make_scrill(project / ".scrills", text=manual("version: 2.0.0") + "PROJECT-MANUAL\n")
    result = scrills(["read", "probe"], home, project)
    assert result.returncode == 0
    assert "PROJECT-MANUAL" in result.stdout and "USER-MANUAL" not in result.stdout
    shutil.rmtree(project / ".scrills" / "probe")
    (project / ".scrills" / "probe.py").write_text('raise RuntimeError("stray executed")\n')
    result = scrills(["read", "probe"], home, project)
    assert result.returncode == 2
    assert "stray module" in result.stderr
    assert "USER-MANUAL" not in result.stdout and "Traceback" not in result.stderr


def test_read_user_layer(home, tmp_path):
    folder = make_scrill(home, name="readuser", text=manual("version: 1.0.0", "readuser"))
    result = scrills(["read", "readuser"], home, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "layer: user" in result.stdout and str(folder) in result.stdout
    assert "=== end README.md ===" in result.stdout
    assert "=== end __init__.py ===" in result.stdout


@pytest.mark.parametrize("location", ["SKILL.md", "docstring"])
def test_old_manual_shapes_are_not_scrills(home, project, location):
    folder = make_scrill(project / ".scrills", text=manual("version: 9.0.0"), location=location)
    with (folder / "__init__.py").open("a") as handle:
        handle.write("\nraise RuntimeError('old format executed')\n")
    listing = scrills(["list"], home, project)
    assert listing.returncode == 0
    assert "README.md is required" in listing.stdout
    assert "- probe: parser probe" not in listing.stdout
    reading = scrills(["read", "probe"], home, project)
    assert reading.returncode == 1
    assert "README.md is required" in reading.stderr
    assert "=== end __init__.py ===" in reading.stdout
    ran = scrills(["run", "probe"], home, project)
    assert ran.returncode == 2
    assert "README.md is required" in ran.stderr
    assert "old format executed" not in ran.stderr
    assert not [r for r in log_records(home, "probe") if r.get("cwd") == str(project)]


def test_read_still_displays_broken_python(tmp_path):
    project = tmp_path / "project"
    source = '"""A helpful entry docstring."""\ndef main(:\n    pass\n'
    make_scrill(project / ".scrills", text=manual("version: 1.0.0"), source=source)
    result = scrills(["read", "probe"], tmp_path / "home", project)
    assert result.returncode == 0
    assert source in result.stdout
    assert "doesn't parse" in result.stderr
    assert "=== end __init__.py ===" in result.stdout


def test_read_decodes_python_encoding_and_markdown_bom(tmp_path):
    project = tmp_path / "project"
    folder = make_scrill(project / ".scrills")
    (folder / "README.md").write_bytes((manual("version: 1.0.0") + "café\n").replace("\n", "\r\n").encode("utf-8-sig"))
    source = '# coding: latin-1\n"""café instructions"""\nVALUE = 1\n'
    (folder / "__init__.py").write_bytes(source.encode("latin-1"))
    result = scrills(["read", "probe"], tmp_path / "home", project)
    assert result.returncode == 0, result.stderr
    assert "café" in result.stdout and source in result.stdout
    assert "no frontmatter" not in result.stderr


def test_read_unreadable_source_reports_partial_output(tmp_path):
    project = tmp_path / "project"
    folder = make_scrill(project / ".scrills", text=manual("version: 1.0.0"))
    entry = folder / "__init__.py"
    entry.write_bytes(b"\xff")
    result = scrills(["read", "probe"], tmp_path / "home", project)
    assert result.returncode == 1
    assert "=== end README.md ===" in result.stdout
    assert "=== end __init__.py ===" not in result.stdout
    assert "cannot read" in result.stderr and str(entry) in result.stderr


@pytest.mark.parametrize("args", [[], ["absent"], ["probe", "extra"], ["../probe"]])
def test_read_refuses_bad_requests(tmp_path, args):
    result = scrills(["read", *args], tmp_path / "home", tmp_path)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "home").exists()


def test_read_names_a_readme_folder_missing_its_entry(tmp_path):
    folder = tmp_path / ".scrills" / "probe"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(manual("version: 1.0.0"))
    home = tmp_path / "home"
    listing = scrills(["list"], home, tmp_path)
    assert "probe" in listing.stdout and "__init__.py" in listing.stdout
    reading = scrills(["read", "probe"], home, tmp_path)
    assert reading.returncode == 2
    assert "__init__.py" in reading.stderr


def test_missing_manifest_helper_refuses_before_registering(home, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for filename in ("scrills", "_boot.py", "_scrills_pth.py"):
        shutil.copy2(Path(CLI).parent / filename, scripts / filename)
    result = subprocess.run(
        [sys.executable, str(scripts / "scrills"), "py"], input="print('must not execute')",
        cwd=tmp_path, env={**base_env(), "SCRILLS_HOME": str(home)}, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "_manifest.py" in result.stderr and "reinstall" in result.stderr
    assert "must not execute" not in result.stdout
    assert not list((Path(home) / ".runs").glob("[0-9]*.json"))


def test_isolated_helper_loading_ignores_cwd_decoys(home, project):
    make_scrill(project / ".scrills", text=manual("version: 1.0.0"))
    (project / "_manifest.py").write_text('raise RuntimeError("cwd helper executed")\n')
    reading = scrills(["read", "probe"], home, project)
    assert reading.returncode == 0, reading.stderr
    ran = scrills(["run", "probe"], home, project)
    assert ran.returncode == 0, ran.stderr
    assert log_records(home, "probe")[-1]["imported"] == {"probe": "1.0.0"}


def test_installer_requires_the_shared_manifest_helper(tmp_path):
    repo = Path(CLI).parent.parent.parent
    clone = tmp_path / "clone"
    for relative in ("install.sh", "scrills/scripts/scrills", "scrills/scripts/_boot.py", "scrills/scripts/_scrills_pth.py", "skills/scrills/SKILL.md"):
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, target)
    bin_dir = tmp_path / "bin"
    result = subprocess.run(["sh", "install.sh", "--no-skill"], cwd=clone,
                            env={**base_env(), "HOME": str(tmp_path / "home"), "SCRILLS_BIN": str(bin_dir)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "_manifest.py" in result.stderr
    assert not (bin_dir / "scrills").exists()


@pytest.fixture(scope="module")
def reader():
    spec = importlib.util.spec_from_file_location("manifest_under_test", Path(CLI).with_name("_manifest.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", [">", "|", ">2-", ">-2", "|2+ # keep", "> # folded", ">bogus"])
def test_every_unquoted_block_indicator_is_rejected(reader, value):
    parsed = reader.parse(manual(f"version: {value}"), "manual-path")
    assert "version" not in parsed.fields
    assert any("manual-path:4:" in notice and "block scalar" in notice for notice in parsed.notices)


@pytest.mark.parametrize("body,field,notice", [
    ("description: a\ndescription: b\nversion: 1", "description", "duplicate description"),
    ("version: 1\n\n# comment\n  more", "version", "continues"),
    ("version: [1, 2]", "version", "structured value"),
    ("version: {value: 1}", "version", "structured value"),
    ("version: &tag 1", "version", "anchor"),
    ("version: *tag", "version", "alias"),
    ('version: "bad\\Nescape"', "version", "quoted"),
    ('version: "1" trailing', "version", "after a quoted value"),
    ('version: "\\ud800"', "version", "one-line"),
    ("version: ''\nmetadata:\n  version: 2", "version", "metadata.version ignored"),
    ("version: >\nmetadata:\n  version: 2", "version", "metadata.version ignored"),
    ("metadata:\n    other: x\n  version: 2", "version", "indentation"),
    ("metadata:\n\tversion: 2", "version", "indentation"),
])
def test_parser_rejects_without_partial_values(reader, body, field, notice):
    parsed = reader.parse(manual(body), "manual-path")
    assert field not in parsed.fields
    assert any(notice in message for message in parsed.notices)


def test_parser_preserves_literal_strings_and_ignores_unconsumed_metadata(reader):
    parsed = reader.parse(manual('description: "read \\"quoted\\" # literal"\nversion: "1" # comment\nmetadata:\n  source:\n    version: 99\nallowed-tools: [anything]'))
    assert parsed.fields["description"] == 'read "quoted" # literal'
    assert parsed.fields["version"] == "1"
    assert not parsed.notices
    assert reader.parse(manual("version: build#42")).fields["version"] == "build#42"


def test_name_drift_diagnostic_points_at_the_declaration(reader, tmp_path):
    entry = tmp_path / "realname" / "__init__.py"
    entry.parent.mkdir()
    (entry.parent / "README.md").write_text("\n\n---\n# comment\nname: other\ndescription: d\nversion: 1\n---\n")
    result = reader.read_manual(str(entry))
    mismatch = next(message for message in result.metadata.notices if "differs from folder" in message)
    assert "README.md:5:" in mismatch


def test_missing_readme_winner_is_not_replaced_with_a_user_scrill(home, project):
    make_scrill(home, name="undocumented", text=manual("version: 1", "undocumented"), source="def main():\n    print('USER EXECUTED')\n")
    make_scrill(project / ".scrills", name="undocumented", source="raise RuntimeError('PROJECT EXECUTED')\n")
    result = scrills(["run", "undocumented"], home, project)
    assert result.returncode == 2
    assert "README.md is required" in result.stderr
    assert "EXECUTED" not in result.stdout + result.stderr
    assert not log_records(home, "undocumented")


def test_read_never_uses_a_foreign_owned_project_layer(home, tmp_path):
    if os.getuid() == 0:
        pytest.skip("needs a different directory owner")
    (tmp_path / ".scrills").symlink_to("/usr")
    make_scrill(home, name="foreignread", text=manual("version: 1", "foreignread"))
    result = scrills(["read", "foreignread"], home, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "layer: user" in result.stdout
    assert "owned by uid 0" in result.stderr


def test_read_reports_permission_denied_without_hiding_the_manual(tmp_path):
    project = tmp_path / "project"
    folder = make_scrill(project / ".scrills", text=manual("version: 1"))
    entry = folder / "__init__.py"
    mode = entry.stat().st_mode
    entry.chmod(0)
    try:
        if os.access(entry, os.R_OK):
            pytest.skip("this user can read mode-000 files")
        result = scrills(["read", "probe"], tmp_path / "home", project)
        assert result.returncode == 1
        assert "cannot read" in result.stderr
        assert "=== end README.md ===" in result.stdout
        assert "=== end __init__.py ===" not in result.stdout
    finally:
        entry.chmod(mode)


def test_versions_are_readme_only_and_still_sampled_at_finish(home, project):
    folder = make_scrill(project / ".scrills", text=manual("version: 1.0.0"))
    code = (
        "from scrills import probe\nfrom pathlib import Path\n"
        "probe.__doc__ = '---\\nversion: 99\\n---'\n"
        f"Path({str(folder / 'README.md')!r}).write_text({manual('version: 2.0.0')!r})\n"
    )
    result = scrills(["py"], home, project, stdin=code)
    assert result.returncode == 0, result.stderr
    assert log_records(home, "py")[-1]["imported"] == {"probe": "2.0.0"}
