"""Tests for the local manager-webhook approval watcher (scripts/approval_watcher)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from omnigent.server import manager_webhook_signing
from omnigent.server.session_outbox import SessionPushContext, build_awaiting_decision_data
from scripts.approval_watcher.policy import evaluate_command
from scripts.approval_watcher.receiver import (
    AWAITING_DECISION,
    AuditLog,
    ResolveResult,
    Watcher,
    serve,
)

CURRENT_SECRET = "current-test-signing-key"
PREVIOUS_SECRET = "previous-test-signing-key"
NOW = 1_800_000_000
SESSION_ID = "sess_watch1"


class FakeResolver:
    """Records resolve calls instead of talking to an Omnigent server."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, path: str, body: dict[str, Any]) -> ResolveResult:
        self.calls.append((path, body))
        return ResolveResult(True, "resolve HTTP 200")


def make_watcher(
    tmp_path: Path, resolver: FakeResolver, secrets: list[str] | None = None
) -> Watcher:
    return Watcher(
        secrets=secrets if secrets is not None else [CURRENT_SECRET],
        resolver=resolver,
        audit=AuditLog(str(tmp_path / "audit.jsonl")),
        workspace_roots=[str(tmp_path)],
        clock=lambda: NOW,
        report=lambda _line: None,
    )


def card_data(request: dict[str, Any], *, elicitation_id: str = "elic_1") -> dict[str, Any]:
    """``session.awaiting_decision`` data built by the server's own payload builder."""
    return build_awaiting_decision_data(
        session_id=SESSION_ID,
        elicitation_id=elicitation_id,
        request=request,
        created_at=NOW,
        context=SessionPushContext(title="watch", agent_name="codex"),
        public_base_url=None,
    )


def codex_command_request(command: str) -> dict[str, Any]:
    return {
        "mode": "form",
        "message": f"Codex wants to run **{command}**",
        "phase": "codex_command_approval",
        "policy_name": "codex_native_command_approval",
    }


def claude_tool_request(tool_name: str) -> dict[str, Any]:
    return {
        "mode": "form",
        "message": f"Claude wants to call **{tool_name}**",
        "phase": "pre_tool_use",
        "policy_name": "claude_native_permission",
        "tool_name": tool_name,
    }


def envelope_body(
    event_id: str, data: dict[str, Any], *, event_type: str = AWAITING_DECISION
) -> bytes:
    """Body shaped like ``manager_webhook_dispatcher.render_delivery_body``."""
    return json.dumps(
        {
            "event_id": event_id,
            "event_version": 1,
            "event_type": event_type,
            "workspace_id": "ws_1",
            "session_id": SESSION_ID,
            "sequence": 1,
            "occurred_at": NOW,
            "data": data,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def signed_headers(
    body: bytes,
    *,
    event_id: str,
    secret: str = CURRENT_SECRET,
    timestamp: int = NOW,
    event_type: str = AWAITING_DECISION,
) -> dict[str, str]:
    signature = manager_webhook_signing.sign(
        secret=secret,
        timestamp=timestamp,
        event_id=event_id,
        raw_json_body=body.decode("utf-8"),
    )
    return manager_webhook_signing.build_headers(
        event_id=event_id,
        event_type=event_type,
        attempt=1,
        timestamp=timestamp,
        key_id=None,
        signature=signature,
    )


def read_audit(tmp_path: Path) -> list[dict[str, Any]]:
    audit_path = tmp_path / "audit.jsonl"
    if not audit_path.exists():
        return []
    return [json.loads(line) for line in audit_path.read_text().splitlines()]


# AC-AP2-01: signature gate and event routing


def test_ac_ap2_01_signed_awaiting_decision_reaches_decision(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body("evt_ok", card_data(codex_command_request("ls /tmp")))

    outcome = watcher.handle(signed_headers(body, event_id="evt_ok"), body)

    assert (outcome.status, outcome.decision) == (200, "approved")


def _wrong_secret(body: bytes) -> dict[str, str]:
    return signed_headers(body, event_id="evt_bad", secret="not-the-key")


def _stale_timestamp(body: bytes) -> dict[str, str]:
    return signed_headers(
        body,
        event_id="evt_bad",
        timestamp=NOW - manager_webhook_signing.DEFAULT_TOLERANCE_SECONDS - 1,
    )


def _missing_signature(body: bytes) -> dict[str, str]:
    headers = signed_headers(body, event_id="evt_bad")
    del headers["X-Omnigent-Signature"]
    return headers


def _missing_timestamp(body: bytes) -> dict[str, str]:
    headers = signed_headers(body, event_id="evt_bad")
    del headers["X-Omnigent-Timestamp"]
    return headers


def _missing_event_id(body: bytes) -> dict[str, str]:
    headers = signed_headers(body, event_id="evt_bad")
    del headers["X-Omnigent-Event-Id"]
    return headers


def _header_event_id_differs_from_body(body: bytes) -> dict[str, str]:
    # Validly signed over the header id, but the envelope names another event.
    return signed_headers(body, event_id="evt_other")


@pytest.mark.parametrize(
    ("make_headers", "expected_status"),
    [
        (_wrong_secret, 401),
        (_stale_timestamp, 401),
        (_missing_signature, 401),
        (_missing_timestamp, 401),
        (_missing_event_id, 401),
        (_header_event_id_differs_from_body, 400),
    ],
    ids=[
        "wrong-secret",
        "stale-timestamp",
        "missing-signature",
        "missing-timestamp",
        "missing-event-id",
        "event-id-mismatch",
    ],
)
def test_ac_ap2_01_rejected_delivery_never_resolves(
    tmp_path: Path, make_headers: Any, expected_status: int
) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body("evt_bad", card_data(codex_command_request("ls /tmp")))

    outcome = watcher.handle(make_headers(body), body)

    assert outcome.status == expected_status
    assert resolver.calls == []
    assert read_audit(tmp_path) == []


def test_ac_ap2_01_previous_key_signature_accepted_during_rotation(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver, secrets=[CURRENT_SECRET, PREVIOUS_SECRET])
    body = envelope_body("evt_rot", card_data(codex_command_request("ls /tmp")))

    outcome = watcher.handle(
        signed_headers(body, event_id="evt_rot", secret=PREVIOUS_SECRET), body
    )

    assert (outcome.status, outcome.decision) == (200, "approved")


@pytest.mark.parametrize("event_type", ["session.completed", "session.failed"])
def test_ac_ap2_01_other_event_types_ignored(tmp_path: Path, event_type: str) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    # Even a card-shaped payload is ignored when the event type is not awaiting_decision.
    body = envelope_body(
        "evt_other_type", card_data(codex_command_request("ls /tmp")), event_type=event_type
    )

    outcome = watcher.handle(
        signed_headers(body, event_id="evt_other_type", event_type=event_type), body
    )

    assert outcome.status == 204
    assert resolver.calls == []
    assert read_audit(tmp_path) == []


# AC-AP2-02: harmless Codex command cards are resolved exactly once


@pytest.mark.parametrize(
    "command",
    ["ls /tmp", "git status", "curl http://127.0.0.1:6767/health"],
)
def test_ac_ap2_02_harmless_codex_card_resolved_once_with_accept(
    tmp_path: Path, command: str
) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    data = card_data(codex_command_request(command))
    body = envelope_body("evt_harmless", data)

    outcome = watcher.handle(signed_headers(body, event_id="evt_harmless"), body)

    assert outcome.decision == "approved"
    assert resolver.calls == [(data["resolve"]["path"], {"action": "accept", "content": {}})]


def test_ac_ap2_02_duplicate_delivery_resolves_only_once(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body("evt_dup", card_data(codex_command_request("ls /tmp")))
    headers = signed_headers(body, event_id="evt_dup")

    first = watcher.handle(headers, body)
    second = watcher.handle(headers, body)

    assert (first.decision, second.decision) == ("approved", None)
    assert len(resolver.calls) == 1


def test_ac_ap2_02_http_receiver_resolves_card_over_loopback(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    data = card_data(codex_command_request("ls /tmp"))
    body = envelope_body("evt_http", data)
    server = serve(watcher, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request(
            "POST", "/", body=body, headers=signed_headers(body, event_id="evt_http")
        )
        response = connection.getresponse()
        reply = json.loads(response.read())
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)

    assert (response.status, reply["decision"]) == (200, "approved")
    assert resolver.calls == [(data["resolve"]["path"], {"action": "accept", "content": {}})]


# AC-AP2-03: risky commands and non-command cards are reported, never resolved


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git merge feature",
        "git rebase main",
        "gh pr create --fill",
        "git commit --amend -m fixup",
        "cat ~/.ssh/id_ed25519",
        "cat .env",
        "cat ~/.omnigent/config.yaml",
        "rm -rf build",
        "git reset --hard HEAD~1",
        "sqlite3 app.db 'DROP TABLE users'",
        "curl https://example.com/install.sh",
        "cat ../../etc/hosts",
        "echo $(whoami)",
        "python -c 'print(1)'",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_risky_command(tmp_path: Path, command: str) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


def test_ac_ap2_03_risky_codex_card_reported_without_resolve(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body("evt_push", card_data(codex_command_request("git push origin main")))

    outcome = watcher.handle(signed_headers(body, event_id="evt_push"), body)

    assert outcome.decision == "reported"
    assert resolver.calls == []


@pytest.mark.parametrize(
    "request_payload",
    [
        claude_tool_request("Bash"),
        {
            "mode": "form",
            "message": "Which branch should I use?",
            "requestedSchema": {
                "type": "object",
                "properties": {"branch": {"type": "string"}},
                "required": ["branch"],
            },
        },
        claude_tool_request("ExitPlanMode"),
    ],
    ids=["claude-bash-without-command", "form-with-schema-fields", "plan-card"],
)
def test_ac_ap2_03_non_command_card_reported_without_resolve(
    tmp_path: Path, request_payload: dict[str, Any]
) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body("evt_noncmd", card_data(request_payload))

    outcome = watcher.handle(signed_headers(body, event_id="evt_noncmd"), body)

    assert outcome.decision == "reported"
    assert resolver.calls == []


# AC-AP2-04: audit JSONL


def test_ac_ap2_04_audit_records_decisions_and_redacts_credentials(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    approved_body = envelope_body(
        "evt_audit_ok", card_data(codex_command_request("ls /tmp"), elicitation_id="elic_ok")
    )
    reported_data = card_data(
        codex_command_request("git push origin main"), elicitation_id="elic_push"
    )
    # Explicit command field bypasses the server's message redaction, so the
    # watcher's own audit redaction is what keeps the token out of the file.
    reported_data["command"] = (
        "git push https://ghp_AbCdEf0123456789AbCdEf0123456789AbCd@github.com/o/r.git"
    )
    reported_body = envelope_body("evt_audit_push", reported_data)

    watcher.handle(signed_headers(approved_body, event_id="evt_audit_ok"), approved_body)
    watcher.handle(signed_headers(reported_body, event_id="evt_audit_push"), reported_body)

    records = read_audit(tmp_path)
    required_fields = {
        "event_id",
        "time",
        "session_id",
        "elicitation_id",
        "decision",
        "command",
        "reason",
    }
    assert all(required_fields <= record.keys() for record in records)
    assert [
        (record["event_id"], record["session_id"], record["elicitation_id"], record["decision"])
        for record in records
    ] == [
        ("evt_audit_ok", SESSION_ID, "elic_ok", "approved"),
        ("evt_audit_push", SESSION_ID, "elic_push", "reported"),
    ]
    assert records[0]["command"] == "ls /tmp"
    assert "[redacted]" in records[1]["command"]
    assert "ghp_" not in (tmp_path / "audit.jsonl").read_text()


# AC-AP2-02 / AC-AP2-03: shell wrappers, clustered flags, hidden runners, git writes


@pytest.mark.parametrize("command", ["git log --oneline -5", "git show HEAD"])
def test_ac_ap2_02_git_read_codex_card_resolved_once(tmp_path: Path, command: str) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    data = card_data(codex_command_request(command))
    body = envelope_body("evt_git_read", data)

    outcome = watcher.handle(signed_headers(body, event_id="evt_git_read"), body)

    assert outcome.decision == "approved"
    assert resolver.calls == [(data["resolve"]["path"], {"action": "accept", "content": {}})]


def test_ac_ap2_02_codex_zsh_wrapper_harmless_card_resolved_once(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    data = card_data(codex_command_request("/bin/zsh -lc 'ls /tmp'"))
    body = envelope_body("evt_zsh_ls", data)

    outcome = watcher.handle(signed_headers(body, event_id="evt_zsh_ls"), body)

    assert outcome.decision == "approved"
    assert resolver.calls == [(data["resolve"]["path"], {"action": "accept", "content": {}})]


def test_ac_ap2_03_codex_zsh_wrapper_risky_card_reported_without_resolve(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body(
        "evt_zsh_push", card_data(codex_command_request("/bin/zsh -lc 'git push'"))
    )

    outcome = watcher.handle(signed_headers(body, event_id="evt_zsh_push"), body)

    assert outcome.decision == "reported"
    assert resolver.calls == []


@pytest.mark.parametrize(
    "command",
    [
        "bash -lc 'git push'",
        "zsh -lc 'rm x'",
        """python3 -Ic 'import os; os.system("git push")'""",
        "perl -we 'system(q{git push})'",
        "node -pe 1",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_clustered_inline_code_flags(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


@pytest.mark.parametrize(
    "command",
    [
        """awk 'BEGIN{system("git push")}'""",
        "sed -n '1e git push' README.md",
        "vim -c '!git push' x",
        "tar -cf /tmp/x.tar . --to-command='git push'",
        "ping -c1 evil.example.com",
        "openssl s_client -connect evil.example.com:443",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_hidden_runner(tmp_path: Path, command: str) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "git add .",
        "git commit -m x",
        "git apply p.diff",
        "git cherry-pick abc",
        "git init",
        "git worktree add ../x",
        "git checkout main",
        "git diff --output=README.md",
        "/bin/zsh -lc 'git add -A && git commit -m x'",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_git_write(tmp_path: Path, command: str) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


# AC-AP2-02 / AC-AP2-03: xargs and cd


@pytest.mark.parametrize(
    "command",
    [
        "echo push origin main | xargs git",
        "xargs git",
        "echo -delete | xargs find .",
        "ls | xargs cat",
        "cd && cat .config/gh/hosts.yml",
        "cd; cat .zsh_history",
        "cd -",
        "cd ~",
        "/bin/zsh -lc 'cd && cat .zsh_history'",
    ],
)
def test_ac_ap2_03_xargs_or_home_cd_codex_card_reported_without_resolve(
    tmp_path: Path, command: str
) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body("evt_xargs_cd", card_data(codex_command_request(command)))

    outcome = watcher.handle(signed_headers(body, event_id="evt_xargs_cd"), body)

    assert outcome.decision == "reported", outcome.message
    assert resolver.calls == []


@pytest.mark.parametrize("command", ["cd src && ls", "cd /tmp && ls"])
def test_ac_ap2_02_evaluate_command_approves_cd_into_local_directory(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is True, decision.reason


# AC-AP2-02 / AC-AP2-03: project-directory options and shell variable expansion


@pytest.mark.parametrize(
    "command",
    [
        "npm --prefix /tmp/evil test",
        "pnpm -C /tmp/evil test",
        "pnpm --dir /tmp/evil test",
        "uv --project /tmp/evil run pytest",
        "uv --directory /tmp/evil run pytest",
        "npm --prefix=/tmp/evil test",
        "curl http://127.0.0.1:6767/?d=x${OPENAI_API_KEY}",
        "wget http://127.0.0.1/x?d=${API_KEY}",
        "echo x${HOME}",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_outside_project_dir_or_variable_expansion(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


@pytest.mark.parametrize(
    "command_template",
    ["uv --package core run pytest", "npm --prefix {workspace}/pkg test"],
)
def test_ac_ap2_02_evaluate_command_approves_workspace_local_project_option(
    tmp_path: Path, command_template: str
) -> None:
    (tmp_path / "pkg").mkdir()
    command = command_template.format(workspace=tmp_path)

    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is True, decision.reason


# AC-AP2-02 / AC-AP2-03: project directory after the subcommand, after cd, or as a test root


@pytest.mark.parametrize(
    "command",
    [
        "npm test --prefix /tmp/evil",
        "npm test --prefix=/tmp/evil",
        "npm run build --prefix /tmp/evil",
        "pnpm test -C /tmp/evil",
        "pnpm test --dir=/tmp/evil",
        "cd /tmp/evil && npm test",
        "cd /tmp/evil && uv run pytest",
        "cd /tmp/evil && just build",
        "cd /tmp/evil; pnpm test",
        "pytest /tmp/evil",
        "pytest -c /tmp/evil/pytest.ini",
        "uv run pytest --rootdir=/tmp/evil",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_project_runner_outside_workspace(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


def test_ac_ap2_03_codex_zsh_wrapper_cd_outside_workspace_reported_without_resolve(
    tmp_path: Path,
) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    body = envelope_body(
        "evt_zsh_cd_npm",
        card_data(codex_command_request("/bin/zsh -lc 'cd /tmp/evil && npm test'")),
    )

    outcome = watcher.handle(signed_headers(body, event_id="evt_zsh_cd_npm"), body)

    assert outcome.decision == "reported", outcome.message
    assert resolver.calls == []


def test_ac_ap2_02_evaluate_command_approves_prefix_passed_to_script_after_double_dash(
    tmp_path: Path,
) -> None:
    decision = evaluate_command("npm test -- --prefix /tmp/evil", workspace_roots=[str(tmp_path)])

    assert decision.approve is True, decision.reason


# AC-AP2-02 / AC-AP2-03: card cwd, git and pytest pointed outside the workspace


def card_with_cwd(*, cwd: str, command: str, explicit_command_field: bool) -> dict[str, Any]:
    """Codex card with ``cwd`` (and optionally ``command``) set on ``data``.

    The server's request allowlist drops both fields, so they are attached
    to ``data`` directly, as a richer producer would send them.
    """
    data = card_data(codex_command_request(command))
    data["cwd"] = cwd
    if explicit_command_field:
        data["command"] = command
    return data


@pytest.mark.parametrize(
    "explicit_command_field", [True, False], ids=["command-field", "codex-message"]
)
def test_ac_ap2_03_card_project_tool_with_cwd_outside_workspace_reported_without_resolve(
    tmp_path: Path, explicit_command_field: bool
) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    data = card_with_cwd(
        cwd="/tmp/evil", command="npm test", explicit_command_field=explicit_command_field
    )
    body = envelope_body("evt_cwd_npm", data)

    outcome = watcher.handle(signed_headers(body, event_id="evt_cwd_npm"), body)

    assert outcome.decision == "reported", outcome.message
    assert resolver.calls == []


def test_ac_ap2_02_card_plain_read_with_tmp_cwd_resolved_once(tmp_path: Path) -> None:
    resolver = FakeResolver()
    watcher = make_watcher(tmp_path, resolver)
    data = card_with_cwd(cwd="/tmp/evil", command="ls", explicit_command_field=True)
    body = envelope_body("evt_cwd_ls", data)

    outcome = watcher.handle(signed_headers(body, event_id="evt_cwd_ls"), body)

    assert outcome.decision == "approved", outcome.message
    assert resolver.calls == [(data["resolve"]["path"], {"action": "accept", "content": {}})]


@pytest.mark.parametrize(
    "command",
    [
        "git -C /tmp/evil status",
        "cd /tmp/evil && git status",
        "cd /tmp/evil && git diff",
        "git --git-dir=/tmp/evil/.git --work-tree=/tmp/evil diff",
        "pytest -o pythonpath=/tmp/evil tests",
        "uv run pytest -p evil -o pythonpath=/tmp/evil",
        "pytest --override-ini=pythonpath=/tmp/evil -p evil",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_git_or_pytest_pointed_outside_workspace(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


@pytest.mark.parametrize(
    "command_template",
    ["git -C {workspace} status", "pytest -p no:cacheprovider -k 'not slow' tests"],
)
def test_ac_ap2_02_evaluate_command_approves_workspace_git_and_plain_pytest(
    tmp_path: Path, command_template: str
) -> None:
    command = command_template.format(workspace=tmp_path)

    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is True, decision.reason


# AC-AP2-02 / AC-AP2-03: pytest short options with `=` and @argfiles


@pytest.mark.parametrize(
    "command",
    [
        "pytest -c=/tmp/evil/pytest.ini",
        "pytest -o=addopts=-s",
        "pytest @/tmp/evil/args",
        "uv run pytest @/tmp/evil/args",
        "pytest @args.txt",
        "pytest -q @/private/tmp/evil/a",
        "pytest -k=@x",
        "pytest -o log_file=/tmp/x.log",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_pytest_equals_option_or_argfile(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


@pytest.mark.parametrize(
    "command",
    ["pytest -p no:cacheprovider -k 'not slow'", "pytest -p=no:cacheprovider -k=slow"],
)
def test_ac_ap2_02_evaluate_command_approves_pytest_disable_plugin_and_keyword(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is True, decision.reason


# AC-AP2-02 / AC-AP2-03: pytest options that delete or overwrite files


@pytest.mark.parametrize(
    "command",
    [
        "pytest --basetemp=.",
        "pytest --basetemp .",
        "pytest --basetemp=tests",
        "pytest --basetemp=/tmp",
        "uv run pytest --basetemp=.",
        "pytest --junitxml=pyproject.toml",
        "pytest --log-file=pyproject.toml",
    ],
)
def test_ac_ap2_03_evaluate_command_reports_pytest_basetemp_or_output_over_project_file(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is False, decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "pytest --junitxml=/tmp/report.xml",
        "pytest --log-file=/dev/null",
        "pytest --lf -x --tb=short --no-header tests",
    ],
)
def test_ac_ap2_02_evaluate_command_approves_pytest_output_to_tmp_and_run_flags(
    tmp_path: Path, command: str
) -> None:
    decision = evaluate_command(command, workspace_roots=[str(tmp_path)])

    assert decision.approve is True, decision.reason
