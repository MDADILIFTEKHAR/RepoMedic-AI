"""User profile formatter."""

def format_user_profile(name: str, email: str):
    """Format user data into expected dictionary contract."""
    # Bug: returns tuple instead of dict
    return (name, email)
