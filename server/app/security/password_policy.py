"""Central password policy validation module.

Owns validating password length, complexity, and disallowing obvious placeholders.
Must NOT store passwords, perform hashing, or query the database directly.
"""


def validate_new_password(password: str, username: str) -> None:
    """Validates that a password satisfies complexity and anti-phishing rules.

    Rules:
    - At least 10 characters long.
    - Not equal to the username (case-insensitive).
    - Must not contain 'CHANGE_ME' (case-insensitive).

    Raises:
        ValueError: If any password rule is violated.
    """
    if not password or len(password) < 10:
        raise ValueError("Password must be at least 10 characters long")

    if username and password.lower() == username.strip().lower():
        raise ValueError("Password cannot match username")

    if "change_me" in password.lower():
        raise ValueError("Password cannot contain 'CHANGE_ME'")
