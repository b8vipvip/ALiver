from app.session_reconciliation import _local_sessions, classify_local_session


def test_generic_avatar_sessions_take_priority():
    metadata = {
        "simli_sessions": {"simli": {"status": "active"}},
        "avatar_sessions": {"vtube": {"status": "active", "provider_type": "vtube_studio"}},
    }

    assert _local_sessions(metadata) == metadata["avatar_sessions"]
    assert classify_local_session(metadata["avatar_sessions"]["vtube"]) is None


def test_old_bridge_metadata_merges_simli_and_vtube_sessions():
    metadata = {
        "simli_sessions": {"simli": {"status": "active"}},
        "vtube_studio_sessions": {"vtube": {"status": "active"}},
    }

    sessions = _local_sessions(metadata)

    assert set(sessions or {}) == {"simli", "vtube"}
