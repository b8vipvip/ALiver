def create_provider(client, name="Managed Mock"):
    response = client.post(
        "/api/providers",
        json={
            "name": name,
            "provider_type": "mock",
            "credentials": {},
            "settings": {},
        },
    )
    assert response.status_code == 201
    return response.json()


def test_provider_force_delete_removes_inactive_session_history(client):
    provider = create_provider(client, "Delete Me")
    session = client.post(
        "/api/sessions",
        json={
            "provider_config_id": provider["id"],
            "overrides": {"_session_name": "待删除会话"},
        },
    ).json()
    stopped = client.post(f"/api/sessions/{session['id']}/stop")
    assert stopped.status_code == 200

    blocked = client.delete(f"/api/providers/{provider['id']}")
    assert blocked.status_code == 409

    deleted = client.delete(f"/api/providers/{provider['id']}?force=true")
    assert deleted.status_code == 204
    assert all(row["id"] != provider["id"] for row in client.get("/api/providers").json())
    assert all(row["id"] != session["id"] for row in client.get("/api/sessions").json())


def test_session_can_be_named_edited_restarted_and_deleted(client):
    provider = create_provider(client, "Session Manager")
    created = client.post(
        "/api/sessions",
        json={
            "provider_config_id": provider["id"],
            "overrides": {
                "_session_name": "首场直播",
                "topic": "hello",
            },
        },
    )
    assert created.status_code == 201
    session = created.json()
    assert session["request"]["_session_name"] == "首场直播"

    renamed = client.patch(
        f"/api/sessions/{session['id']}",
        json={"name": "首场直播-改名"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["request"]["_session_name"] == "首场直播-改名"

    stopped = client.post(f"/api/sessions/{session['id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "ended"

    edited = client.patch(
        f"/api/sessions/{session['id']}",
        json={"overrides": {"topic": "updated"}},
    )
    assert edited.status_code == 200
    assert edited.json()["request"]["topic"] == "updated"
    assert edited.json()["request"]["_session_name"] == "首场直播-改名"

    restarted = client.post(f"/api/sessions/{session['id']}/restart")
    assert restarted.status_code == 200
    assert restarted.json()["status"] == "active"

    blocked_delete = client.delete(f"/api/sessions/{session['id']}")
    assert blocked_delete.status_code == 409

    client.post(f"/api/sessions/{session['id']}/stop")
    deleted = client.delete(f"/api/sessions/{session['id']}")
    assert deleted.status_code == 204


def test_director_command_can_be_edited_and_deleted(client):
    registration = client.post(
        "/api/director/extensions/register",
        json={
            "name": "Test Director",
            "browser_name": "Chrome",
            "version": "1.0",
            "metadata": {},
        },
    )
    assert registration.status_code == 201
    extension_id = registration.json()["extension_id"]

    created = client.post(
        "/api/director/commands",
        json={
            "extension_id": extension_id,
            "command_type": "director_instruction",
            "content": "先说第一句话",
            "wrap_as_director": True,
            "auto_send": True,
            "force": False,
            "priority": 50,
            "source": "test",
        },
    )
    assert created.status_code == 201
    command = created.json()
    assert command["status"] == "queued"

    edited = client.patch(
        f"/api/director/commands/{command['id']}",
        json={
            "content": "改成第二句话",
            "priority": 80,
            "wrap_as_director": True,
        },
    )
    assert edited.status_code == 200
    body = edited.json()
    assert body["priority"] == 80
    assert "改成第二句话" in body["payload"]["text"]
    assert body["payload"]["content"] == "改成第二句话"

    deleted = client.delete(f"/api/director/commands/{command['id']}")
    assert deleted.status_code == 204
    assert all(
        row["id"] != command["id"]
        for row in client.get("/api/director/commands").json()
    )
