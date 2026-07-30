from bridge.douyin_visible_runtime_patch import parse_visible_lines


def register_and_configure(client):
    extension = client.post(
        "/api/director/extensions/register",
        json={"name": "Douyin Visible Chrome", "browser_name": "Chrome", "version": "0.1.1"},
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


def register_bridge(client):
    response = client.post(
        "/api/bridges/register",
        json={
            "name": "Visible Collector Bridge",
            "machine_name": "windows-test",
            "version": "0.8.0",
            "capabilities": ["douyin.visible.hybrid"],
            "metadata": {},
        },
    )
    assert response.status_code == 201
    return response.json()


def test_parser_merges_ocr_nickname_and_comment_on_same_row():
    events = parse_visible_lines(
        [
            {"text": "小雪", "source": "ocr", "confidence": 0.97, "bbox": [10, 100, 60, 122]},
            {
                "text": "数字人直播怎么实现？",
                "source": "ocr",
                "confidence": 0.95,
                "bbox": [70, 99, 260, 123],
            },
        ]
    )
    assert len(events) == 1
    assert events[0]["event_type"] == "comment"
    assert events[0]["user_name"] == "小雪"
    assert events[0]["content"] == "数字人直播怎么实现？"
    assert events[0]["source"] == "ocr"


def test_parser_maps_visible_follow_gift_and_optional_join():
    lines = [
        {"text": "小田田 关注了你", "source": "uia", "confidence": 1.0},
        {"text": "阿明 送出了小心心 × 2", "source": "uia", "confidence": 1.0},
        {"text": "七彩虹 来了", "source": "uia", "confidence": 1.0},
    ]
    default_events = parse_visible_lines(lines)
    assert [item["event_type"] for item in default_events] == ["follow", "gift"]
    with_join = parse_visible_lines(lines, capture_join_notices=True)
    assert [item["event_type"] for item in with_join] == ["follow", "gift", "system"]


def test_bridge_ingests_visible_events_and_deduplicates(client):
    extension_id = register_and_configure(client)
    bridge = register_bridge(client)
    headers = {"X-Bridge-Token": bridge["token"]}

    heartbeat = client.post(
        "/api/douyin-live/bridge/heartbeat",
        headers=headers,
        json={
            "bridge_id": bridge["bridge_id"],
            "extension_id": extension_id,
            "collector_id": "visible-test",
            "connected": True,
            "mode": "hybrid",
            "window_title": "直播伴侣 · 抖音",
            "uia_available": True,
            "ocr_available": True,
            "active_source": "uia",
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["connected"] is True

    payload = {
        "bridge_id": bridge["bridge_id"],
        "extension_id": extension_id,
        "collector_id": "visible-test",
        "metadata": {
            "mode": "hybrid",
            "active_source": "uia",
            "window_title": "直播伴侣 · 抖音",
            "uia_available": True,
            "ocr_available": True,
        },
        "events": [
            {
                "event_id": "visible-comment-1",
                "event_type": "comment",
                "user_name": "小雪",
                "content": "数字人直播怎么实现？",
                "source": "uia",
                "confidence": 1.0,
                "raw_text": "小雪 数字人直播怎么实现？",
            },
            {
                "event_id": "visible-gift-1",
                "event_type": "gift",
                "user_name": "阿明",
                "content": "送出了小心心 × 2",
                "source": "ocr",
                "confidence": 0.96,
                "raw_text": "阿明 送出了小心心 × 2",
            },
            {
                "event_id": "visible-follow-1",
                "event_type": "follow",
                "user_name": "新观众",
                "content": "关注了直播间",
                "source": "uia",
                "confidence": 1.0,
                "raw_text": "新观众 关注了你",
            },
            {
                "event_id": "low-confidence",
                "event_type": "comment",
                "user_name": "模糊昵称",
                "content": "无法确定的文字",
                "source": "ocr",
                "confidence": 0.45,
                "confidence_threshold": 0.72,
                "raw_text": "模糊文字",
            },
        ],
    }
    first = client.post("/api/douyin-live/bridge/ingest", headers=headers, json=payload)
    assert first.status_code == 200
    assert first.json()["accepted"] == 3
    assert first.json()["ignored"] == 1

    second = client.post("/api/douyin-live/bridge/ingest", headers=headers, json=payload)
    assert second.status_code == 200
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 3
    assert second.json()["ignored"] == 1

    events = client.get(f"/api/auto-director/events?extension_id={extension_id}&limit=20").json()
    assert {item["event_type"] for item in events} >= {"comment", "gift", "follow"}
    assert all(item["platform"] == "douyin_visible_ui" for item in events)

    status = client.get(f"/api/douyin-live/status?extension_id={extension_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["connected"] is True
    assert body["mode"] == "hybrid"
    assert body["counts"]["comment"] == 1
    assert body["counts"]["gift"] == 1
    assert body["counts"]["follow"] == 1
    assert body["capture_policy"] == "visible_ui_only"


def test_rejects_invalid_bridge_token(client):
    extension_id = register_and_configure(client)
    bridge = register_bridge(client)
    response = client.post(
        "/api/douyin-live/bridge/heartbeat",
        headers={"X-Bridge-Token": "invalid"},
        json={
            "bridge_id": bridge["bridge_id"],
            "extension_id": extension_id,
            "connected": True,
            "mode": "hybrid",
        },
    )
    assert response.status_code == 401
