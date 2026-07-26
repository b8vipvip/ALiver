from app.security import decrypt_json, encrypt_json, generate_token, hash_token, verify_token


def test_encryption_round_trip():
    encrypted = encrypt_json({"api_key": "secret"})
    assert "secret" not in encrypted
    assert decrypt_json(encrypted) == {"api_key": "secret"}


def test_bridge_token_hashing():
    token = generate_token()
    digest = hash_token(token)
    assert verify_token(token, digest)
    assert not verify_token(token + "x", digest)
