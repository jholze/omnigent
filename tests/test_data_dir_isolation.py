"""Regression coverage for pytest runtime-data isolation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from _pytest.outcomes import Failed

import tests.conftest as suite_conftest
from omnigent import cli
from omnigent.host import local_server


def test_pytest_uses_an_isolated_omnigent_data_dir() -> None:
    """Daemon state used by tests must never resolve under the real home."""
    test_data_dir = Path(os.environ["OMNIGENT_DATA_DIR"]).resolve()
    real_data_dir = (Path.home() / ".omnigent").resolve()

    assert test_data_dir != real_data_dir
    assert local_server._LOCAL_SERVER_PID_PATH.resolve().is_relative_to(test_data_dir)
    assert cli._HOST_PID_PATH.resolve().is_relative_to(test_data_dir)
    assert cli._daemon_registry_dir().resolve().is_relative_to(test_data_dir)


def test_pytest_config_home_is_outside_the_login_account() -> None:
    """Config writes must not land in the login account's ~/.omnigent."""
    if os.name == "nt":
        login_home = Path.home().resolve()
    else:
        import pwd

        login_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    config_home = Path(os.environ["OMNIGENT_CONFIG_HOME"]).resolve()

    assert config_home != (login_home / ".omnigent").resolve()
    assert not config_home.is_relative_to(login_home / ".omnigent")


def test_login_config_guard_fails_when_the_file_changes(tmp_path: Path) -> None:
    """The guard fails when a test rewrites the watched config file."""
    target = tmp_path / "config.yaml"
    target.write_text("server: http://127.0.0.1:6767\n", encoding="utf-8")
    before = suite_conftest._config_fingerprint(target)
    target.write_text("server: http://localhost:8000\n", encoding="utf-8")
    with pytest.raises(Failed, match="login account"):
        suite_conftest._assert_login_config_unchanged(target, before)


def test_login_config_guard_allows_an_unchanged_file(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("server: http://127.0.0.1:6767\n", encoding="utf-8")
    before = suite_conftest._config_fingerprint(target)
    suite_conftest._assert_login_config_unchanged(target, before)


def test_pytest_does_not_inherit_debug_log_delivery_credentials() -> None:
    """Pytest startup also isolates the log sink in processes spawned by tests."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OMNIGENT_DEBUG_LOG_")
    }
    env.update(
        OMNIGENT_DEBUG_LOG_CLIENT_ID="test-client",
        OMNIGENT_DEBUG_LOG_CLIENT_SECRET="test-secret",
        OMNIGENT_DEBUG_LOG_WORKSPACE_URL="https://workspace.example.test",
        OMNIGENT_DEBUG_LOG_ENDPOINT=(
            "https://123.zerobus.example.test/zerobus/v1/tables/catalog.schema.logs/insert"
        ),
    )
    check = "from omnigent.debug_logging import config_from_env; assert config_from_env() is None"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tests.conftest; import subprocess, sys; "
            f"{check}; subprocess.run([sys.executable, '-c', {check!r}], check=True)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
