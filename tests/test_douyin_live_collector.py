def register_and_configure(client):
    extension = client.post(
        "/api/director/extensions/register",
        json={"name": "Douyin Collector Chrome", "browser_name": "Chrome", "version": "0.1.1"},
    ).json()
    response = client.put(
        "/api/auto-director/config",
        json={
            "extension_id": extension["extension_id"],
            "enabled": True,
            "mode": "rules",
            "settings": {
                "professional_mode": True,
                "min_score": 35,
                "rundown": [
                    {
                        "id": "opening",
                        "name": "开场",
                        "duration_seconds": 60,
                        "objective": "欢迎观众",
                        "cue": "向大家问好",
                        "avatar_action": "wave",
                    },
                    {
                        "id": "closing",
                        "name": "收尾",
                        "duration_seconds": 60,
                        "objective": "结束直播",
                        "cue": "感谢观众",
                        "avatar_action": "happy",
                    },
                ],
            },
        },
    )
    assert response.status_code == 200
    return extension["extension_id"]


def test_ingests_official_open_live_data(client):
    extension_id = register_and_configure(client)
    payload = {
        "extension_id": extension_id,
        "collector_id": "test-collector",
        "event_name": "OPEN_LIVE_DATA",
        "metadata": {"mate_version": "8.4.0", "layout_mode": 0},
        "payload": [
            {
                "msg_id": "comment-1",
                "timestamp": 1,
                "msg_type": 2,
                "msg_type_str": "live_comment",
                "nickname": "小雪",
                "content": "数字人直播怎么实现？",
            },
            {
                "msg_id": "gift-1",
                "timestamp": 2,
                "msg_type": 3,
                "msg_type_str": "live_gift",
                "nickname": "阿明",
                "gift_name": "小心心",
                "gift_num": 2,
            },
            {
                "msg_id": "follow-1",
                "timestamp": 3,
                "msg_type": 5,
                "msg_type_str": "live_follow",
                "nickname": "新观众",
                "user_follow_action": 1,
            },
            {
                "msg_id": "unfollow-1",
                "timestamp": 4,
                "msg_type": 5,
                "msg_type_str": "live_follow",
                "nickname": "离开的观众",
                "user_follow_action": 2,
            },
        ],
    }
    response = client.post("/api/douyin-live/ingest", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["received"] == 4
    assert result["accepted"] == 3
    assert result["ignored"] == 1

    events = client.get(f"/api/auto-director/events?extension_id={extension_id}&limit=20").json()
    assert {item["event_type"] for item in events} >= {"comment", "gift", "follow"}
    gift = next(item for item in events if item["event_type"] == "gift")
    assert "小心心 × 2" in gift["content"]

    status = client.get(f"/api/douyin-live/status?extension_id={extension_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["connected"] is True
    assert body["counts"]["live_comment"] == 1
    assert body["counts"]["live_gift"] == 1
    assert body["share_support"] == "compatibility_only"


def test_deduplicates_by_douyin_message_id(client):
    extension_id = register_and_configure(client)
    payload = {
        "extension_id": extension_id,
        "event_name": "OPEN_LIVE_DATA",
        "payload": [
            {
                "msg_id": "same-message",
                "timestamp": 1,
                "msg_type_str": "live_comment",
                "nickname": "观众甲",
                "content": "同一条消息",
            }
        ],
    }
    first = client.post("/api/douyin-live/ingest", json=payload).json()
    second = client.post("/api/douyin-live/ingest", json=payload).json()
    assert first["accepted"] == 1
    assert second["accepted"] == 0
    assert second["duplicates"] == 1


def test_rejects_ingest_before_auto_director_is_configured(client):
    extension = client.post(
        "/api/director/extensions/register",
        json={"name": "Unconfigured", "browser_name": "Chrome", "version": "0.1.1"},
    ).json()
    response = client.post(
        "/api/douyin-live/ingest",
        json={
            "extension_id": extension["extension_id"],
            "event_name": "OPEN_LIVE_DATA",
            "payload": [{"msg_id": "1", "msg_type_str": "live_comment", "content": "你好"}],
        },
    )
    assert response.status_code == 409
