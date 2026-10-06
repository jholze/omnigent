"""Raising the process's soft open-file limit at startup."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable, Iterable

import pytest

from omnigent.util.open_file_limit import (
    _FALLBACK_SOFT_LIMITS,
    DEFAULT_SOFT_OPEN_FILE_LIMIT,
    OpenFileLimit,
    raise_soft_open_file_limit,
)

# Linux's ``resource.RLIM_INFINITY``; macOS exposes a large positive sentinel instead.
_INFINITY = -1
_LOGGER_NAME = "omnigent.util.open_file_limit"


class _FakeResource:
    """Stand-in for ``resource`` that records ``setrlimit`` calls and rejects chosen values."""

    RLIMIT_NOFILE = 7
    RLIM_INFINITY = _INFINITY

    def __init__(self, soft: int, hard: int, *, reject: Iterable[int] = ()) -> None:
        self.limits = (soft, hard)
        self.reject = set(reject)
        self.calls: list[tuple[int, int]] = []

    def getrlimit(self, which: int) -> tuple[int, int]:
        assert which == self.RLIMIT_NOFILE
        return self.limits

    def setrlimit(self, which: int, limits: tuple[int, int]) -> None:
        assert which == self.RLIMIT_NOFILE
        self.calls.append(limits)
        if limits[0] in self.reject:
            raise ValueError("current limit exceeds maximum limit")
        self.limits = limits


@pytest.fixture
def fake_resource(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _FakeResource]:
    """Install a fake ``resource`` module the helper imports lazily."""

    def install(soft: int, hard: int, *, reject: Iterable[int] = ()) -> _FakeResource:
        fake = _FakeResource(soft, hard, reject=reject)
        monkeypatch.setitem(sys.modules, "resource", fake)
        return fake

    return install


def test_raises_soft_limit_to_the_target_under_a_finite_hard_limit(
    fake_resource: Callable[..., _FakeResource], caplog: pytest.LogCaptureFixture
) -> None:
    fake = fake_resource(256, 65536)

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        result = raise_soft_open_file_limit()

    assert result == OpenFileLimit(65536, 65536)
    assert fake.calls == [(65536, 65536)]
    assert "raised soft open-file limit from 256 to 65536 (hard 65536)" in caplog.text


def test_finite_hard_limit_caps_the_target(fake_resource: Callable[..., _FakeResource]) -> None:
    fake = fake_resource(256, 4096)

    assert raise_soft_open_file_limit() == OpenFileLimit(4096, 4096)
    assert fake.calls == [(4096, 4096)]


def test_unlimited_hard_limit_falls_back_to_macos_open_max(
    fake_resource: Callable[..., _FakeResource], caplog: pytest.LogCaptureFixture
) -> None:
    """macOS rejects a soft limit above kern.maxfilesperproc even with an unlimited hard limit."""
    fake = fake_resource(256, _INFINITY, reject={DEFAULT_SOFT_OPEN_FILE_LIMIT})

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        result = raise_soft_open_file_limit()

    assert result == OpenFileLimit(10240, _INFINITY)
    assert fake.calls == [(65536, _INFINITY), (10240, _INFINITY)]
    assert "raised soft open-file limit from 256 to 10240 (hard unlimited)" in caplog.text


@pytest.mark.parametrize("soft", [DEFAULT_SOFT_OPEN_FILE_LIMIT, 70000, _INFINITY])
def test_sufficient_soft_limit_is_left_alone(
    fake_resource: Callable[..., _FakeResource], soft: int
) -> None:
    fake = fake_resource(soft, _INFINITY)

    assert raise_soft_open_file_limit() == OpenFileLimit(soft, _INFINITY)
    assert fake.calls == []


def test_tuned_kernel_below_open_max_settles_on_a_smaller_step(
    fake_resource: Callable[..., _FakeResource],
) -> None:
    """A host with kern.maxfilesperproc below OPEN_MAX still gets headroom."""
    fake = fake_resource(256, _INFINITY, reject={65536, 10240})

    assert raise_soft_open_file_limit() == OpenFileLimit(4096, _INFINITY)
    assert fake.calls == [(65536, _INFINITY), (10240, _INFINITY), (4096, _INFINITY)]


def test_rejected_attempts_warn_and_keep_the_inherited_limit(
    fake_resource: Callable[..., _FakeResource], caplog: pytest.LogCaptureFixture
) -> None:
    fake = fake_resource(256, _INFINITY, reject={65536, 10240, 4096, 1024})

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        result = raise_soft_open_file_limit()

    assert result == OpenFileLimit(256, _INFINITY)
    assert [wanted for wanted, _ in fake.calls] == [65536, 10240, 4096, 1024]
    assert "could not raise soft open-file limit from 256 (hard unlimited)" in caplog.text


def test_rejected_attempt_above_open_max_is_informational(
    fake_resource: Callable[..., _FakeResource], caplog: pytest.LogCaptureFixture
) -> None:
    """An inherited limit already above OPEN_MAX leaves headroom, so a rejected raise is INFO."""
    fake = fake_resource(12000, _INFINITY, reject={DEFAULT_SOFT_OPEN_FILE_LIMIT})

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        result = raise_soft_open_file_limit()

    assert result == OpenFileLimit(12000, _INFINITY)
    assert fake.calls == [(65536, _INFINITY)]
    assert [r.levelno for r in caplog.records if r.name == _LOGGER_NAME] == [logging.INFO]


def test_platform_without_rlimits_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no ``resource`` module; startup must not depend on it."""
    monkeypatch.setitem(sys.modules, "resource", None)

    assert raise_soft_open_file_limit() is None


def test_real_process_raises_its_own_soft_limit() -> None:
    """Against the live kernel: a process inheriting a soft limit of 256 raises it at startup.

    Runs in a child so the rlimit mutation never touches the test runner.
    """
    pytest.importorskip("resource")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, resource\n"
            "from omnigent.util.open_file_limit import raise_soft_open_file_limit\n"
            "_soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
            "resource.setrlimit(resource.RLIMIT_NOFILE, (min(_soft, 256), hard))\n"
            "raised = raise_soft_open_file_limit()\n"
            "print(json.dumps([list(raised), list(resource.getrlimit(resource.RLIMIT_NOFILE)),"
            " hard == resource.RLIM_INFINITY]))\n",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    raised, live, hard_unlimited = json.loads(child.stdout)
    soft, hard = live
    if not hard_unlimited and hard <= 256:
        pytest.skip(f"hard RLIMIT_NOFILE ({hard}) is too low to raise the soft limit above 256")
    # macOS may reject 65536 under an unlimited hard limit and settle on a fallback.
    accepted = (
        {DEFAULT_SOFT_OPEN_FILE_LIMIT, *_FALLBACK_SOFT_LIMITS}
        if hard_unlimited
        else {min(hard, DEFAULT_SOFT_OPEN_FILE_LIMIT)}
    )
    assert raised == live
    assert soft in accepted and soft > 256
