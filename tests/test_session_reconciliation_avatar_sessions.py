from app.session_reconciliation import _local_sessions, classify_local_session


def test_generic_avatar_sessions_take_priority():
    metadata = {
        "avatar_sessions": {"vtube": {"status": "active", "provider_type": "vtube_studio"}},
        "vtube_studio_sessions": {"legacy-vtube": {"status": "active"}},
    }

    assert _local_sessions(metadata) == metadata["avatar_sessions"]
    assert classify_local_session(metadata["avatar_sessions"]["vtube"]) is None


def test_old_bridge_metadata_uses_vtube_sessions_only():
    metadata = {"vtube_studio_sessions": {"vtube": {"status": "active"}}}

    sessions = _local_sessions(metadata)

    assert set(sessions or {}) == {"vtube"}
