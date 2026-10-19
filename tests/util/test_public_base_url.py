"""Tests for resolving the public base URL used in user-facing links."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from omnigent.util import public_base_url


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point global config at an empty temp home with no env override.

    :param tmp_path: Pytest temp dir; also the cwd so no project config loads.
    :param monkeypatch: Pytest monkeypatch fixture.
    :returns: Directory whose ``config.yaml`` is the global config.
    """
    home = tmp_path / "config-home"
    home.mkdir()
    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(public_base_url.PUBLIC_BASE_URL_ENV_VAR, raising=False)
    monkeypatch.setattr(public_base_url, "_warned_invalid", set())
    return home


def test_normalize_strips_whitespace_and_trailing_slash() -> None:
    """Surrounding whitespace and trailing slashes are removed."""
    assert (
        public_base_url.normalize_public_base_url("  https://omnigent.example.com/sub//  ")
        == "https://omnigent.example.com/sub"
    )


@pytest.mark.parametrize("raw", [None, "", "   ", "/"])
def test_normalize_treats_blank_as_unset(raw: str | None) -> None:
    """A missing or blank value means no public base URL."""
    assert public_base_url.normalize_public_base_url(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("ftp://omnigent.example.com", id="non-http-scheme"),
        pytest.param("omnigent.example.com", id="missing-scheme"),
        pytest.param("https://", id="missing-host"),
        pytest.param("https://user:secret@omnigent.example.com", id="credentials"),
        pytest.param("https://omnigent.example.com/?next=1", id="query"),
        pytest.param("https://omnigent.example.com/#top", id="fragment"),
        pytest.param(42, id="non-string"),
    ],
)
def test_normalize_rejects_invalid_values(raw: object) -> None:
    """Only a plain http(s) origin (optionally with a path) is accepted."""
    with pytest.raises(ValueError):
        public_base_url.normalize_public_base_url(raw)


def test_configured_reads_global_config_key(config_home: Path) -> None:
    """The ``public_base_url`` config key applies when the env var is unset."""
    (config_home / "config.yaml").write_text("public_base_url: https://omnigent.example.com/\n")

    assert public_base_url.configured_public_base_url() == "https://omnigent.example.com"


def test_configured_env_var_beats_config_key(
    config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``OMNIGENT_PUBLIC_BASE_URL`` overrides the config key."""
    (config_home / "config.yaml").write_text("public_base_url: https://config.example.com\n")
    monkeypatch.setenv(public_base_url.PUBLIC_BASE_URL_ENV_VAR, "https://env.example.com")

    assert public_base_url.configured_public_base_url() == "https://env.example.com"


def test_configured_unset_returns_none(config_home: Path) -> None:
    """No env var and no config key means no public base URL."""
    assert public_base_url.configured_public_base_url() is None


def test_configured_invalid_value_is_ignored_and_warned_once(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An invalid value yields ``None`` and logs a single warning across calls."""
    monkeypatch.setenv(public_base_url.PUBLIC_BASE_URL_ENV_VAR, "ftp://omnigent.example.com")

    with caplog.at_level(logging.WARNING, logger=public_base_url.__name__):
        first = public_base_url.configured_public_base_url()
        second = public_base_url.configured_public_base_url()

    assert (first, second) == (None, None)
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "local_base",
    ["http://127.0.0.1:6767", "http://localhost:6767/", "http://[::1]:6767"],
)
def test_public_link_base_rewrites_loopback_bases(
    config_home: Path, monkeypatch: pytest.MonkeyPatch, local_base: str
) -> None:
    """Every loopback spelling is replaced by the configured public base."""
    monkeypatch.setenv(public_base_url.PUBLIC_BASE_URL_ENV_VAR, "https://omnigent.example.com")

    assert public_base_url.public_link_base(local_base) == "https://omnigent.example.com"


@pytest.mark.parametrize(
    "remote_base",
    ["https://remote.example.org", "http://192.0.2.10:6767", "http://0.0.0.0:6767"],
)
def test_public_link_base_keeps_non_loopback_bases(
    config_home: Path, monkeypatch: pytest.MonkeyPatch, remote_base: str
) -> None:
    """A non-loopback server base is never rewritten."""
    monkeypatch.setenv(public_base_url.PUBLIC_BASE_URL_ENV_VAR, "https://omnigent.example.com")

    assert public_base_url.public_link_base(remote_base) is None


def test_public_link_base_unset_keeps_loopback_base(config_home: Path) -> None:
    """With nothing configured, loopback links are left to the caller."""
    assert public_base_url.public_link_base("http://127.0.0.1:6767") is None
