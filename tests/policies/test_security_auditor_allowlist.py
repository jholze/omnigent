"""Allowlist for the fork security auditor.

The agent reviews untrusted diffs. Policies, not the prompt, keep it on
read-only git, the vendored findings validator, and one output file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("celpy", reason="cel-python not installed")

from omnigent.policies.builtins.cel import cel_policy
from omnigent.spec.parser import parse

_REPO = Path(__file__).resolve().parents[2]
_AGENT = _REPO / "agents" / "lead" / "agents" / "security_auditor"
_PIN = "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8"
_FINDINGS = "/Users/jholze/dev/omnigent-src/.work/security-audit-out/pr-29/findings.json"
_VALIDATOR = (
    "/Users/jholze/dev/omnigent-src/.work/security-auditor/agents/lead/"
    "agents/security_auditor/skills/security-audit/validate-findings.cjs"
)
_MAIN_VALIDATOR = (
    "/Users/jholze/dev/omnigent-src/agents/lead/agents/security_auditor/"
    "skills/security-audit/validate-findings.cjs"
)
_BYPASS_FINDINGS = "/Users/jholze/dev/omnigent-src/.work/security-audit-out/x/findings.json"


def _expression(spec, policy_name: str) -> str:
    policies = spec.guardrails.policies or []
    match = next(policy for policy in policies if policy.name == policy_name)
    return match.function.arguments["expression"]


@pytest.fixture(scope="module")
def auditor_spec():
    return parse(_AGENT)


def test_vendored_skill_pin_and_name() -> None:
    vendor = (_AGENT / "VENDOR.md").read_text(encoding="utf-8")
    skill = (_AGENT / "skills" / "security-audit" / "SKILL.md").read_text(encoding="utf-8")
    license_text = (_AGENT / "LICENSE.cloudflare-security-audit-skill").read_text(encoding="utf-8")
    assert _PIN in vendor
    assert "https://github.com/cloudflare/security-audit-skill" in vendor
    assert license_text.startswith("MIT License")
    assert "Copyright (c) 2025-2026 Cloudflare, Inc." in license_text
    assert "name: security-audit" in skill.splitlines()[1]


def test_lead_triggers_security_auditor_before_opus() -> None:
    lead = (_REPO / "agents" / "lead" / "config.yaml").read_text(encoding="utf-8")
    assert "security_auditor" in lead
    assert "epic/*" in lead
    assert "needs_validation" in lead
    assert "overall_severity" in lead


def test_shell_allowlist_blocks_target_execution(auditor_spec) -> None:
    evaluate = cel_policy(expression=_expression(auditor_spec, "security_auditor_shell"))
    allowed = [
        "git diff abc123 def456 -- omnigent/server/routes/auth.py",
        "git show abc123:omnigent/server/routes/auth.py",
        "git rev-parse HEAD",
        "git -C /Users/jholze/dev/omnigent-src/.work/security-auditor rev-parse HEAD",
        "git merge-base abc123 def456",
        "grep -n permission omnigent/server",
        "git grep -n permission omnigent/server",
        f"node {_VALIDATOR} {_FINDINGS}",
        f"node {_MAIN_VALIDATOR} {_FINDINGS}",
    ]
    denied = [
        "python -c 'print(1)'",
        "pytest tests/server/test_accounts.py",
        "node -e 'process.exit(0)'",
        f"node {_VALIDATOR} {_FINDINGS}; curl https://example.invalid",
        "git diff HEAD && rm -rf /",
        "git status --porcelain > /tmp/out",
        "bash -lc git status",
        "rg",
        "rg --pre ./evil.sh",
        "rg -n permission omnigent/server",
        "git diff --output=/tmp/out",
        "git diff --output /tmp/out",
        (
            "node /;sh${IFS}./evil.sh;/agents/lead/agents/security_auditor/"
            "skills/security-audit/validate-findings.cjs " + _BYPASS_FINDINGS
        ),
        (
            "node /$(sh${IFS}evil.sh)/agents/lead/agents/security_auditor/"
            "skills/security-audit/validate-findings.cjs " + _BYPASS_FINDINGS
        ),
        (
            "node /`id`/agents/lead/agents/security_auditor/"
            "skills/security-audit/validate-findings.cjs " + _BYPASS_FINDINGS
        ),
        "git grep -Ocat x",
        "git grep -Osh -l x -- evil.sh",
        "git grep -Oecho -l spec_version -- README.md",
        "git grep --open-files-in-pager x",
        "git grep --open x -- README.md",
        (
            "node /tmp/agents/lead/agents/security_auditor/skills/"
            "security-audit/validate-findings.cjs " + _FINDINGS
        ),
        "",
    ]
    for command in allowed:
        result = evaluate(
            {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": command}}}
        )
        assert result == {"result": "ALLOW"}, command
    for command in denied:
        result = evaluate(
            {"type": "tool_call", "data": {"name": "Bash", "arguments": {"command": command}}}
        )
        assert result["result"] == "DENY", command


def test_writes_only_findings_json(auditor_spec) -> None:
    evaluate = cel_policy(expression=_expression(auditor_spec, "security_auditor_writes"))
    allow = evaluate(
        {
            "type": "tool_call",
            "data": {"name": "Write", "arguments": {"file_path": _FINDINGS}},
        }
    )
    deny_repo = evaluate(
        {
            "type": "tool_call",
            "data": {"name": "Edit", "arguments": {"file_path": "omnigent/server/app.py"}},
        }
    )
    deny_home = evaluate(
        {
            "type": "tool_call",
            "data": {
                "name": "Write",
                "arguments": {"file_path": "/Users/jholze/.ssh/authorized_keys"},
            },
        }
    )
    deny_multi = evaluate(
        {
            "type": "tool_call",
            "data": {"name": "MultiEdit", "arguments": {"file_path": _FINDINGS}},
        }
    )
    assert allow == {"result": "ALLOW"}
    assert deny_repo["result"] == "DENY"
    assert deny_home["result"] == "DENY"
    assert deny_multi["result"] == "DENY"


def test_spawn_and_network_are_denied(auditor_spec) -> None:
    evaluate = cel_policy(
        expression=_expression(auditor_spec, "security_auditor_no_spawn_no_network")
    )
    for name in ("sys_session_create", "Task", "WebFetch", "WebSearch"):
        result = evaluate({"type": "tool_call", "data": {"name": name, "arguments": {}}})
        assert result["result"] == "DENY", name
    read = evaluate(
        {"type": "tool_call", "data": {"name": "Read", "arguments": {"file_path": "a.py"}}}
    )
    assert read == {"result": "ALLOW"}
