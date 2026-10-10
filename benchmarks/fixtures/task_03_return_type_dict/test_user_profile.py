from user_profile import format_user_profile

def test_profile_dict_contract():
    res = format_user_profile("Alice", "alice@example.com")
    assert isinstance(res, dict)
    assert res["name"] == "Alice"
    assert res["email"] == "alice@example.com"
