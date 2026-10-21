"""The demo seed plan stays stable and the credentials file stays private."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_team_chat_demo.py"


def _load():
    spec = importlib.util.spec_from_file_location("seed_team_chat_demo", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_demo_plan_is_safe_and_complete() -> None:
    seed = _load()
    plan = seed.demo_plan()
    seed.validate_plan(plan)

    assert plan.users == ("jens", "anna", "bob")
    assert [channel.name for channel in plan.channels] == ["produkt", "design", "betrieb"]
    assert all(channel.thread.replies for channel in plan.channels)
    assert plan.direct.opener == "jens" and plan.direct.other == "anna"
    assert plan.agent.channel == "produkt"
    assert plan.agent.root.client_id == "demo-agent-root"
    assert plan.agent.reply_text.startswith("Ich warte auf die Freigabe")


def test_validate_plan_rejects_an_agent_mention() -> None:
    seed = _load()
    plan = seed.demo_plan()
    mentioned = seed.SeedMessage("demo-produkt-welcome", "jens", "bitte <@omnigent_agent:agent>")
    channel = plan.channels[0]
    broken = seed.SeedChannel(
        name=channel.name,
        topic=channel.topic,
        messages=(mentioned, *channel.messages[1:]),
        thread=channel.thread,
    )
    plan = seed.SeedPlan(
        users=plan.users,
        admin=plan.admin,
        pointer=plan.pointer,
        channels=(broken, *plan.channels[1:]),
        direct=plan.direct,
        agent=plan.agent,
    )
    with pytest.raises(ValueError, match="agent mention"):
        seed.validate_plan(plan)


def test_passwords_ignore_unknown_keys_and_require_every_user() -> None:
    seed = _load()
    text = "# comment\njens=one\nanna=two\nbob=three\nCOOKIE_SECRET=not-a-user\n"
    assert seed.passwords_from_text(text) == {"jens": "one", "anna": "two", "bob": "three"}
    with pytest.raises(ValueError, match="bob"):
        seed.passwords_from_text("jens=one\nanna=two\n")


def test_elicitation_id_matches_the_permission_hook() -> None:
    seed = _load()
    elicitation_id = seed.new_elicitation_id()
    assert seed._ELICITATION_ID_RE.fullmatch(elicitation_id)


def test_client_drops_session_cookies_so_bearer_wins() -> None:
    seed = _load()
    api = seed.DemoApi("http://127.0.0.1", {"jens": "a", "anna": "b", "bob": "c"})
    api.http.cookies.set("__Host-ap_session", "session")
    api._forget_cookies()
    assert list(api.http.cookies) == []
    api.close()


def test_credentials_file_must_be_mode_600(tmp_path: Path) -> None:
    seed = _load()
    path = tmp_path / "credentials"
    path.write_text("jens=x\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    with pytest.raises(ValueError, match="mode 600"):
        seed.assert_private_file(path)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    seed.assert_private_file(path)


def _fake_chat_server(events: list[dict]) -> httpx.MockTransport:
    """Answer the calls seed() makes; the thread shows an agent item once the reply event lands."""
    channels: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else {}
        if (method, path) == ("POST", "/auth/login"):
            return httpx.Response(200, json={"token": f"fake-{body['username']}"})
        if (method, path) == ("GET", "/v1/chat/bootstrap"):
            listed = [{"id": "ch-general", "name": "general"}]
            listed += [{"id": cid, "name": name} for name, cid in channels.items()]
            return httpx.Response(200, json={"channels": listed})
        if (method, path) == ("POST", "/v1/chat/channels"):
            if body["name"] in channels:
                return httpx.Response(409, json={"error": "exists"})
            channels[body["name"]] = f"ch-{body['name']}"
            return httpx.Response(201, json={"id": channels[body["name"]]})
        if method == "POST" and path.endswith("/join"):
            return httpx.Response(204)
        if method == "POST" and path.endswith("/messages"):
            return httpx.Response(201, json={"id": f"msg-{body['client_id']}"})
        if method == "GET" and path.endswith("/messages"):
            items = [{"id": "msg-demo-agent-root", "author_kind": "user", "body": "root"}]
            if any(event["type"] == "external_assistant_message" for event in events):
                items.append({"id": "msg-agent", "author_kind": "omnigent_agent", "body": "ok"})
            return httpx.Response(200, json={"items": items})
        if (method, path) == ("POST", "/v1/chat/dms"):
            return httpx.Response(200, json={"id": "dm-jens-anna"})
        if (method, path) == ("PUT", "/v1/sessions/sess-demo/permissions"):
            return httpx.Response(204)
        if (method, path) == ("POST", "/v1/sessions/sess-demo/events"):
            events.append(body)
            return httpx.Response(202)
        if (method, path) == ("GET", "/v1/sessions/sess-demo"):
            return httpx.Response(200, json={"pending_elicitations": []})
        return httpx.Response(599, json={"error": f"unexpected {method} {path}"})

    return httpx.MockTransport(handle)


def test_second_seed_run_does_not_repost_the_agent_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _load()
    plan = seed.demo_plan()
    monkeypatch.setattr(seed, "linked_session_id", lambda _uri, _root: "sess-demo")

    def _no_link(*_args, **_kwargs) -> None:
        raise AssertionError("link_session must not run for an already linked thread")

    monkeypatch.setattr(seed, "link_session", _no_link)
    events: list[dict] = []
    api = seed.DemoApi("http://demo.test", {"jens": "a", "anna": "b", "bob": "c"})
    api.http = httpx.Client(transport=_fake_chat_server(events))

    try:
        assert seed.seed(api, plan, "sqlite://", hold=False) == "seeded"
        assert seed.seed(api, plan, "sqlite://", hold=False) == "seeded"
    finally:
        api.close()

    replies = [event for event in events if event["type"] == "external_assistant_message"]
    idle = [
        event
        for event in events
        if event["type"] == "external_session_status"
        and event["data"] == {"status": "idle", "response_id": plan.agent.response_id}
    ]
    assert len(replies) == 1
    assert len(idle) == 1
