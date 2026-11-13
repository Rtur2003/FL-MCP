import json

from flmcp_service import webhooks
from tests.service.conftest import SECRET, START

DAY = 86400
ADMIN = {"Authorization": "Bearer admin-secret"}


def issue(svc, email="a@example.test", plan="pro", days=None):
    body = {"email": email, "plan": plan, **({"days": days} if days else {})}
    r = svc.http.post("/v1/admin/licenses", json=body, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["key"]


def activate(svc, key, device="device-0001", name="studio pc", **extra):
    return svc.http.post(
        "/v1/activate", json={"key": key, "device_id": device, "name": name, **extra}
    )


def login(svc, **kw):
    r = activate(svc, issue(svc, **kw))
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def reserve(svc, auth, ops=("produce.compose",), idem="idem-0000001"):
    return svc.http.post("/v1/credits/reserve", json={"ops": list(ops), "idem": idem}, headers=auth)


def event(svc, ev, secret=SECRET, t=None, provider="generic"):
    body = json.dumps(ev).encode()
    sig = webhooks.sign(secret, body, svc.clock.t if t is None else t)
    return svc.http.post(
        f"/v1/webhooks/{provider}", content=body, headers={"X-FLMCP-Signature": sig}
    )


def test_activation_returns_the_plan_balance_and_prices(svc):
    r = activate(svc, issue(svc))
    body = r.json()
    assert r.status_code == 200 and body["plan"] == "pro" and body["balance"] == 300
    assert body["costs"]["produce.compose"] == 5 and len(body["token"]) >= 40


def test_a_mistyped_or_unknown_key_is_explained(svc):
    key = issue(svc)
    typo = key[:-1] + ("A" if key[-1] != "A" else "B")
    r = activate(svc, typo)
    assert r.status_code == 400 and r.json()["code"] == "bad_key"
    assert r.headers["content-type"].startswith("application/problem+json")
    from fl_mcp.commerce import keys

    fresh = keys.new_key()
    r = activate(svc, fresh)
    assert r.status_code == 404 and r.json()["code"] == "unknown_key"


def test_keys_are_stored_hashed_and_tokens_too(svc):
    key = issue(svc)
    token = activate(svc, key).json()["token"]
    with svc.db.tx() as c:
        dump = "".join(
            str(tuple(r)) for t in ("licenses", "devices") for r in c.execute(f"SELECT * FROM {t}")
        )
    from fl_mcp.commerce import keys

    assert keys.normalize(key) not in dump and token not in dump


def test_account_shows_balance_devices_and_the_topup_link(svc):
    auth = login(svc)
    body = svc.http.get("/v1/account", headers=auth).json()
    assert body["balance"] == 300 and body["devices"][0]["name"] == "studio pc"
    assert body["topup_url"] == "https://example.test/topup"


def test_missing_or_wrong_tokens_are_401(svc):
    r = svc.http.get("/v1/account")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    r = svc.http.get("/v1/account", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"


def test_activating_again_rotates_the_token(svc):
    key = issue(svc)
    first = activate(svc, key).json()["token"]
    second = activate(svc, key).json()["token"]
    assert first != second
    assert (
        svc.http.get("/v1/account", headers={"Authorization": f"Bearer {first}"}).status_code == 401
    )
    assert (
        svc.http.get("/v1/account", headers={"Authorization": f"Bearer {second}"}).status_code
        == 200
    )


def test_device_limit_is_enforced_and_can_be_resolved_by_replacing(svc):
    key = issue(svc)  # pro: 2 devices
    t1 = activate(svc, key, "device-0001", "laptop").json()["token"]
    activate(svc, key, "device-0002", "desktop")
    r = activate(svc, key, "device-0003", "new pc")
    assert r.status_code == 409 and r.json()["code"] == "device_limit"
    assert {d["name"] for d in r.json()["devices"]} == {"laptop", "desktop"}
    r = activate(svc, key, "device-0003", "new pc", replace="laptop")
    assert r.status_code == 200
    old = svc.http.get("/v1/account", headers={"Authorization": f"Bearer {t1}"})
    assert old.status_code == 401, "the replaced device is signed out"


def test_replace_must_name_exactly_one_device(svc):
    key = issue(svc, plan="pro")
    activate(svc, key, "device-aaaa1", "same")
    activate(svc, key, "device-aaaa2", "same")
    r = activate(svc, key, "device-0003", "x", replace="same")
    assert r.status_code == 409 and r.json()["code"] == "ambiguous_replace"
    r = activate(svc, key, "device-0003", "x", replace="nothing")
    assert r.status_code == 409 and r.json()["code"] == "device_limit"


def test_signing_out_frees_the_seat(svc):
    key = issue(svc, plan="trial")  # one device
    token = activate(svc, key, "device-0001").json()["token"]
    assert activate(svc, key, "device-0002").status_code == 409
    r = svc.http.post("/v1/devices/self/revoke", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204
    assert activate(svc, key, "device-0002").status_code == 200


def test_reserve_and_settle_over_http(svc):
    auth = login(svc)
    r = reserve(svc, auth, ["produce.compose", "produce.flp"])
    held = r.json()
    assert r.status_code == 200 and held["cost"] == 8 and held["balance"] == 292
    s = svc.http.post(
        f"/v1/credits/{held['id']}/settle", json={"done": ["produce.compose"]}, headers=auth
    )
    assert s.json()["charged"] == 5 and s.json()["balance"] == 295
    assert svc.http.get("/v1/account", headers=auth).json()["balance"] == 295


def test_running_out_of_credits_is_a_402_with_a_way_forward(svc):
    auth = login(svc, plan="trial")  # 15 credits
    for i in range(3):
        assert reserve(svc, auth, idem=f"idem-000000{i}").status_code == 200
    r = reserve(svc, auth, idem="idem-0000009")
    body = r.json()
    assert r.status_code == 402 and body["code"] == "insufficient_credits"
    assert (body["cost"], body["balance"], body["topup_url"]) == (
        5,
        0,
        "https://example.test/topup",
    )


def test_the_trial_ends(svc):
    auth = login(svc, plan="trial")
    svc.clock.advance(31 * DAY)
    r = svc.http.get("/v1/account", headers=auth)
    assert r.status_code == 403 and r.json()["code"] == "license_inactive"


def test_bad_requests_get_a_readable_422(svc):
    auth = login(svc)
    r = svc.http.post("/v1/credits/reserve", json={"ops": [], "idem": "x"}, headers=auth)
    assert r.status_code == 422 and r.json()["code"] == "bad_request"
    assert r.headers["content-type"].startswith("application/problem+json")
    r = svc.http.post("/v1/activate", json={"key": "k"})
    assert r.status_code == 422


def test_webhook_lifecycle(svc):
    start = START
    end = START + 30 * DAY
    started = {
        "id": "e1",
        "type": "subscription.started",
        "email": "new@example.test",
        "plan": "pro",
        "ref": "sub_1",
        "period_start": start,
        "period_end": end,
    }
    assert event(svc, started).status_code == 200
    assert event(svc, started).json() == {"duplicate": True}
    with svc.db.tx() as c:
        mail = c.execute("SELECT payload FROM outbox").fetchone()
    key = json.loads(mail["payload"])["key"]
    auth = {"Authorization": "Bearer " + activate(svc, key).json()["token"]}
    assert svc.http.get("/v1/account", headers=auth).json()["balance"] == 300

    svc.clock.advance(30 * DAY)
    renewed = {
        "id": "e2",
        "type": "subscription.renewed",
        "ref": "sub_1",
        "period_start": end,
        "period_end": end + 30 * DAY,
    }
    assert event(svc, renewed).status_code == 200
    assert svc.http.get("/v1/account", headers=auth).json()["balance"] == 300, "allowance, not 600"
    renewed_again = {**renewed, "id": "e3"}  # a different event for the same period
    assert event(svc, renewed_again).status_code == 200
    assert svc.http.get("/v1/account", headers=auth).json()["balance"] == 300

    assert (
        event(svc, {"id": "e4", "type": "subscription.canceled", "ref": "sub_1"}).status_code == 200
    )
    assert svc.http.get("/v1/account", headers=auth).status_code == 200, "paid period still works"
    svc.clock.advance(31 * DAY)
    assert svc.http.get("/v1/account", headers=auth).json()["code"] == "license_inactive"


def test_failed_payments_pause_access_until_the_next_renewal(svc):
    ev = {
        "id": "e1",
        "type": "subscription.started",
        "email": "p@example.test",
        "plan": "pro",
        "ref": "sub_9",
        "period_start": START,
        "period_end": START + 30 * DAY,
    }
    event(svc, ev)
    with svc.db.tx() as c:
        key = json.loads(c.execute("SELECT payload FROM outbox").fetchone()["payload"])["key"]
    auth = {"Authorization": "Bearer " + activate(svc, key).json()["token"]}
    event(svc, {"id": "e2", "type": "subscription.past_due", "ref": "sub_9"})
    assert svc.http.get("/v1/account", headers=auth).status_code == 403
    event(
        svc,
        {
            "id": "e3",
            "type": "subscription.renewed",
            "ref": "sub_9",
            "period_start": START + 30 * DAY,
            "period_end": START + 60 * DAY,
        },
    )
    assert svc.http.get("/v1/account", headers=auth).status_code == 200


def test_plan_change_and_expiry_events(svc):
    event(
        svc,
        {
            "id": "e1",
            "type": "subscription.started",
            "email": "c@example.test",
            "plan": "pro",
            "ref": "sub_5",
            "period_start": START,
            "period_end": START + 30 * DAY,
        },
    )
    r = event(
        svc, {"id": "e2", "type": "subscription.plan_changed", "ref": "sub_5", "plan": "studio"}
    )
    assert r.status_code == 200
    r = event(
        svc, {"id": "e3", "type": "subscription.plan_changed", "ref": "sub_5", "plan": "gold"}
    )
    assert r.status_code == 400 and r.json()["code"] == "unknown_plan"
    event(svc, {"id": "e4", "type": "subscription.expired", "ref": "sub_5"})
    with svc.db.tx() as c:
        assert c.execute("SELECT status, plan FROM licenses").fetchone()[:] == ("revoked", "studio")


def test_events_that_arrive_out_of_order_are_retried_not_lost(svc):
    r = event(
        svc,
        {
            "id": "e9",
            "type": "subscription.renewed",
            "ref": "sub_missing",
            "period_start": START,
            "period_end": START + DAY,
        },
    )
    assert r.status_code == 409 and r.json()["code"] == "unknown_subscription"
    with svc.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0, "can be retried"


def test_topups_add_credits_once(svc):
    auth = login(svc, email="t@example.test", plan="trial")
    top = {"id": "pay_1", "type": "topup.paid", "email": "t@example.test", "pack": "pack_100"}
    assert event(svc, top).status_code == 200
    assert event(svc, top).json() == {"duplicate": True}
    assert svc.http.get("/v1/account", headers=auth).json()["balance"] == 115
    assert event(svc, {**top, "id": "pay_2", "pack": "pack_999"}).json()["code"] == "unknown_pack"


def test_webhook_signatures_are_enforced(svc):
    ev = {"id": "x", "type": "topup.paid", "email": "t@example.test", "pack": "pack_100"}
    assert event(svc, ev, secret="wrong").status_code == 401
    assert event(svc, ev, t=svc.clock.t - 3600).status_code == 401, "replays are refused"
    assert event(svc, ev, provider="nobody").status_code == 404
    r = svc.http.post("/v1/webhooks/generic", content=b"{}")
    assert r.status_code == 401
    body = b"not json"
    sig = webhooks.sign(SECRET, body, svc.clock.t)
    r = svc.http.post("/v1/webhooks/generic", content=body, headers={"X-FLMCP-Signature": sig})
    assert r.status_code == 400
    assert event(svc, {"id": "y", "type": "refund.paid"}).json()["code"] == "unknown_event"


def test_the_admin_api_needs_its_token(svc):
    body = {"email": "a@example.test", "plan": "pro"}
    assert svc.http.post("/v1/admin/licenses", json=body).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert svc.http.post("/v1/admin/licenses", json=body, headers=bad).status_code == 401
    off = svc.app  # without a token configured the admin API does not exist
    from fastapi.testclient import TestClient

    from flmcp_service.app import Settings, create_app

    quiet = TestClient(create_app(Settings(db=svc.settings.db), svc.clock))
    assert quiet.post("/v1/admin/licenses", json=body, headers=ADMIN).status_code == 404
    assert off is not None


def test_admin_grants_and_lookup(svc):
    issue(svc, email="g@example.test", plan="trial")
    r = svc.http.post(
        "/v1/admin/grants", json={"email": "g@example.test", "credits": 50}, headers=ADMIN
    )
    assert r.json()["balance"] == 65
    info = svc.http.get(
        "/v1/admin/accounts", params={"email": "G@example.test"}, headers=ADMIN
    ).json()
    assert info["balance"] == 65 and info["licenses"][0]["plan"] == "trial"
    assert [e["kind"] for e in info["entries"]] == ["grant", "grant"]
    assert (
        svc.http.get("/v1/admin/accounts", params={"email": "x@y.z"}, headers=ADMIN).status_code
        == 404
    )


def test_odd_header_bytes_are_refused_not_crashed_on(svc):
    r = svc.http.post(
        "/v1/webhooks/generic", content=b"{}", headers={"X-FLMCP-Signature": b"t=1,v1=\xe9\xe8"}
    )
    assert r.status_code == 401
    r = svc.http.post(
        "/v1/admin/licenses",
        json={"email": "a@example.test", "plan": "pro"},
        headers={"Authorization": b"Bearer \xe9\xe8"},
    )
    assert r.status_code == 401


def test_a_licence_whose_plan_was_removed_is_explained(svc):
    from fastapi.testclient import TestClient

    from flmcp_service.app import Settings, create_app

    key = issue(svc)
    config = svc.settings.db + ".json"
    open(config, "w").write(json.dumps({"plans": {"trial": {"credits": 5, "devices": 1}}}))
    other = TestClient(create_app(Settings(db=svc.settings.db, config=config), svc.clock))
    r = other.post("/v1/activate", json={"key": key, "device_id": "device-0001", "name": "x"})
    assert r.status_code == 409 and r.json()["code"] == "unknown_plan"


def test_an_oversized_webhook_is_refused_before_it_is_read(svc):
    r = svc.http.post(
        "/v1/webhooks/generic",
        content=b"x" * (webhooks_limit() + 1),
        headers={"X-FLMCP-Signature": "t=1,v1=00"},
    )
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def webhooks_limit():
    from flmcp_service.app import MAX_WEBHOOK

    return MAX_WEBHOOK


def test_webhook_settings_and_bad_content_length(svc, monkeypatch):
    from flmcp_service.app import Settings

    monkeypatch.setenv("FLMCP_WEBHOOK_SECRETS", "invalid_no_eq, generic = sec1 , =empty,")
    settings = Settings.from_env()
    assert settings.webhook_secrets == {"generic": "sec1"}

    for bad_len in ("not-a-number", "-1"):
        r = svc.http.post(
            "/v1/webhooks/generic",
            content=b"{}",
            headers={"Content-Length": bad_len, "X-FLMCP-Signature": "t=1,v1=00"},
        )
        assert r.status_code == 400 and r.json()["code"] == "bad_request"
