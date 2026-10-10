from validator import validate_password

def test_password_strength():
    assert validate_password("Secr3tPassword!") is True
    assert validate_password("abc") is False
    assert validate_password("passwordwithoutdigit") is False
