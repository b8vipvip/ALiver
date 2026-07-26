def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_mock_provider_and_session(client):
    providers = client.get("/api/providers").json()
    assert any(row["provider_type"] == "mock" for row in providers)
    mock = next(row for row in providers if row["provider_type"] == "mock")

    test_response = client.post(f"/api/providers/{mock['id']}/test")
    assert test_response.status_code == 200
    assert test_response.json()["success"] is True

    session_response = client.post(
        "/api/sessions",
        json={"provider_config_id": mock["id"], "overrides": {"topic": "hello"}},
    )
    assert session_response.status_code == 201
    body = session_response.json()
    assert body["status"] == "active"
    assert body["external_session_id"].startswith("mock-")

    stop_response = client.post(f"/api/sessions/{body['id']}/stop")
    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "ended"


def test_provider_credentials_are_redacted(client):
    response = client.post(
        "/api/providers",
        json={
            "name": "Tavus test",
            "provider_type": "tavus",
            "credentials": {"api_key": "super-secret"},
            "settings": {"persona_id": "p1"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["credential_keys"] == ["api_key"]
    assert "super-secret" not in response.text
