def register_extension(client):
    response = client.post(
        "/api/director/extensions/register",
        json={"name": "Test Chrome", "browser_name": "Chrome", "version": "0.1.0"},
    )
    assert response.status_code == 201
    return response.json()


def test_director_command_queues_and_wraps_text(client):
    extension = register_extension(client)
    response = client.post(
        "/api/director/commands",
        json={
            "extension_id": extension["extension_id"],
            "command_type": "director_instruction",
            "content": "回答观众的问题，控制在20秒以内。",
            "wrap_as_director": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["payload"]["text"].startswith("【导演指令】")
    assert "回答观众的问题" in body["payload"]["text"]


def test_extension_receives_queued_command_and_reports_result(client):
    extension = register_extension(client)
    command = client.post(
        "/api/director/commands",
        json={
            "extension_id": extension["extension_id"],
            "command_type": "send_text",
            "content": "你好，直播间。",
            "wrap_as_director": False,
        },
    ).json()

    with client.websocket_connect(
        f"/ws/extensions/{extension['extension_id']}?token={extension['token']}"
    ) as websocket:
        welcome = websocket.receive_json()
        assert welcome["type"] == "welcome"
        dispatched = websocket.receive_json()
        assert dispatched["type"] == "director.command"
        assert dispatched["command_id"] == command["id"]
        assert dispatched["payload"]["text"] == "你好，直播间。"
        websocket.send_json(
            {
                "type": "command.result",
                "command_id": command["id"],
                "ok": True,
                "data": {"sent": True},
            }
        )
        ack = websocket.receive_json()
        assert ack["type"] == "command.result.ack"
        assert ack["command_id"] == command["id"]
        assert ack["status"] == "completed"

    rows = client.get("/api/director/commands").json()
    row = next(item for item in rows if item["id"] == command["id"])
    assert row["status"] == "completed"
    assert row["result"]["sent"] is True


def test_extension_hello_refreshes_version(client):
    extension = register_extension(client)
    with client.websocket_connect(
        f"/ws/extensions/{extension['extension_id']}?token={extension['token']}"
    ) as websocket:
        assert websocket.receive_json()["type"] == "welcome"
        websocket.send_json(
            {
                "type": "extension.hello",
                "metadata": {
                    "extension_version": "0.1.1",
                    "runtime_id": "test-runtime",
                },
            }
        )
        assert websocket.receive_json()["type"] == "pong"
        rows = client.get("/api/director/extensions").json()
        row = next(item for item in rows if item["id"] == extension["extension_id"])
        assert row["version"] == "0.1.1"
        assert row["metadata"]["runtime_id"] == "test-runtime"
