"""Password strength validator."""

def validate_password(password: str) -> bool:
    # Bug: overly lenient
    return len(password) > 3
