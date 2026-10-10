from auth_token import verify_token

def test_valid_token():
    assert verify_token("tok_secure_live_99214") is True

def test_invalid_token():
    assert verify_token("unverified") is False
    assert verify_token("") is False
