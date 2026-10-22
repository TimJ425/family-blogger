"""Shared test helpers for creating family members.

Public sign-up only creates the first account (the admin); admins add
everyone else. ``join_family`` follows the same rule so the suites
keep their old "sign this person up" intent: the first member signs
up through ``/signup`` and later members are created as an admin's
"Add member" form would create them, then log in.

Only for apps with ``WTF_CSRF_ENABLED`` set to ``False``.
"""
from flask import Flask
from flask.testing import FlaskClient

from family_blogger.constants import DEFAULT_PRESET
from family_blogger.models import User, db


def add_member(app: Flask, username: str, password: str = "secret1") -> int:
    """Create a non-admin member directly, like "Add member" does.

    Args:
        app: The application under test.
        username: The new member's username.
        password: Their starting password.

    Returns:
        The new member's id.
    """
    with app.app_context():
        user = User(username=username, is_admin=False)
        user.set_password(password)
        user.apply_theme(DEFAULT_PRESET)
        db.session.add(user)
        db.session.commit()
        return user.id


def join_family(
    app: Flask, username: str, password: str = "secret1"
) -> FlaskClient:
    """Bring a member into the family and return their logged-in client.

    The first member signs up through ``/signup`` and becomes the
    admin. Later members are added directly and then log in. Either
    way the member still has onboarding to finish.

    Args:
        app: The application under test.
        username: The member's username.
        password: The member's password.

    Returns:
        A test client holding the member's session.

    Raises:
        AssertionError: If the sign-up or log-in did not succeed.
    """
    with app.app_context():
        first = db.session.scalar(db.select(db.func.count(User.id))) == 0
    client = app.test_client()
    if first:
        r = client.post(
            "/signup",
            data={
                "username": username,
                "password": password,
                "confirm_password": password,
            },
        )
    else:
        add_member(app, username, password)
        r = client.post(
            "/login", data={"username": username, "password": password}
        )
    if r.status_code != 302:
        raise AssertionError(f"join {username} -> {r.status_code}")
    return client
