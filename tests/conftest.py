# One session-scoped user home for the whole suite: a single venv build shared by every file.
# base_env drops the developer's own SCRILLS_* variables and TRACEPARENT so they can't steer
# the suite.
import os
import subprocess
import sys
from pathlib import Path

import pytest

BOOTSTRAP_CLI = str(Path(__file__).resolve().parent.parent / "scrills" / "scripts" / "scrills")


def base_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("SCRILLS_") and key != "TRACEPARENT"}


@pytest.fixture(scope="session")
def home(tmp_path_factory):
    path = tmp_path_factory.mktemp("home")
    result = subprocess.run(
        [sys.executable, BOOTSTRAP_CLI, "py"],
        input="1",
        env={**base_env(), "SCRILLS_HOME": str(path)},
        cwd=str(path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return path
