from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jobpipe.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def initialized_workspace(workspace: Path, runner: CliRunner) -> Path:
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout
    return workspace
