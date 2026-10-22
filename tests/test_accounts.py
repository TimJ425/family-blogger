"""Accounts: closed sign-up, "Add member", sessions and migration 1.

Covers the first-admin sign-up, admins adding members, session tokens
(old cookies, password resets and changes, deleted users), user ids
never being reused, the 30-day remember-me cookie and the migration
that adds ``user.session_token`` to an existing database.
"""
import atexit
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient
from flask_login.utils import encode_cookie

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.auth.routes import _create_first_admin  # noqa: E402
from family_blogger.migrations import (  # noqa: E402
    latest_version,
    user_sessions_and_ids,
)
from family_blogger.models import User, db  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="fb-accounts-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
CSRF_RE = r'name="csrf_token"[^>]*value="([^"]+)"'
passed = 0


def check(cond: object, msg: str) -> None:
    """Assert that a condition holds and count it as a passed check.

    Args:
        cond: The condition, which must be truthy.
        msg: The failure message.

    Raises:
        AssertionError: If the condition is falsy.
    """
    global passed
    if not cond:
        raise AssertionError(msg)
    passed += 1


def make_app(name: str, csrf: bool = False) -> Flask:
    """Build the real app with its own instance folder.

    Args:
        name: Folder name inside the scratch directory.
        csrf: Whether CSRF protection is switched on.

    Returns:
        The configured app.
    """
    return create_app({
        "INSTANCE_DIR": str(Path(SCRATCH) / name),
        "TESTING": True,
        "WTF_CSRF_ENABLED": csrf,
    })


def user_count(app: Flask) -> int:
    """Count the users in an app's database.

    Args:
        app: The application.

    Returns:
        The number of users.
    """
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(User.id)))


def find_user(app: Flask, username: str) -> User | None:
    """Look a user up by username.

    Args:
        app: The application.
        username: The username (any case).

    Returns:
        The user, detached from its session, or ``None``.
    """
    with app.app_context():
        user = User.find_by_username(username)
        if user is not None:
            db.session.expunge(user)
        return user


def signup(client: FlaskClient, username: str,
           password: str = "secret1") -> object:
    """Post the public sign-up form.

    Args:
        client: The test client.
        username: Username to sign up with.
        password: Password, typed twice.

    Returns:
        The response.
    """
    return client.post("/signup", data={
        "username": username,
        "password": password,
        "confirm_password": password,
    })


def login(app: Flask, username: str, password: str = "secret1",
          remember: bool = False) -> FlaskClient:
    """Log in with a new client and check it worked.

    Args:
        app: The application.
        username: Username.
        password: Password.
        remember: Whether to tick "Keep me logged in".

    Returns:
        The logged-in client.
    """
    c = app.test_client()
    data = {"username": username, "password": password}
    if remember:
        data["remember"] = "y"
    r = c.post("/login", data=data)
    check(r.status_code == 302 and "/login" not in r.location,
          f"login {username}")
    return c


def onboard(client: FlaskClient) -> None:
    """Finish onboarding with simple choices.

    Args:
        client: The member's logged-in client.
    """
    r = client.post(
        "/welcome",
        data={"display_name": "", "avatar": "fox",
              "theme_preset": "ocean"},
        content_type="multipart/form-data",
    )
    check(r.status_code == 302, "onboarding saved")


def add_member(admin: FlaskClient, username: str,
               password: str = "secret1",
               confirm: str | None = None) -> object:
    """Post the admin "Add member" form.

    Args:
        admin: An admin's logged-in client.
        username: New username.
        password: Starting password.
        confirm: Confirmation, defaulting to ``password``.

    Returns:
        The response.
    """
    return admin.post("/admin/members", data={
        "username": username,
        "password": password,
        "confirm_password": password if confirm is None else confirm,
    })


def signed_in(client: FlaskClient) -> bool:
    """Say whether a client is still signed in.

    Args:
        client: The test client.

    Returns:
        ``True`` if the feed loads instead of redirecting to log in.
    """
    r = client.get("/")
    return r.status_code == 200


# --- first sign-up and closed sign-up ------------------------------
app = make_app("main")
anon = app.test_client()
html = anon.get("/login").get_data(as_text=True)
check("/signup" in html, "login links to sign-up while there are no users")
check(anon.get("/signup").status_code == 200, "sign-up open when empty")

mum = app.test_client()
r = signup(mum, "Mum")
check(r.status_code == 302 and r.location.endswith("/welcome"),
      "first sign-up goes to onboarding")
check(find_user(app, "mum").is_admin, "first sign-up becomes admin")
check(find_user(app, "mum").theme_preset == "sunny", "default theme")
onboard(mum)

late = app.test_client()
r = late.get("/signup")
text = r.get_data(as_text=True)
check(r.status_code == 403
      and "Ask a grown-up in your family to add you" in text
      and 'href="/login"' in text,
      "GET sign-up closed once users exist")
r = signup(late, "sneaky")
check(r.status_code == 403, "POST sign-up closed once users exist")
check(find_user(app, "sneaky") is None and user_count(app) == 1,
      "closed sign-up creates nobody")
html = late.get("/login").get_data(as_text=True)
check("/signup" not in html and "Ask a grown-up" in html,
      "login hides sign-up link once users exist")

# The first-admin insert re-checks inside its transaction, so a
# sign-up that slipped past the early check cannot become admin.
with app.test_request_context():
    check(_create_first_admin("racer", "secret1") is None,
          "second 'first' sign-up refused inside the transaction")
check(find_user(app, "racer") is None and user_count(app) == 1,
      "racing sign-up left no row")

# --- admin adds members --------------------------------------------
html = mum.get("/admin").get_data(as_text=True)
check('action="/admin/members' in html and 'for="member-username"' in html
      and 'for="member-password"' in html
      and 'for="member-confirm"' in html,
      "admin page shows labelled Add member form")
form_html = html[html.index('action="/admin/members'):]
form_html = form_html[:form_html.index("</form>")]
check('name="csrf_token"' in form_html, "Add member form has CSRF token")
check("make admin" not in form_html.lower(), "no make-admin option")

r = add_member(mum, "Kid")
check(r.status_code == 302 and r.location.endswith("/admin"),
      "add member redirects to admin")
kid = find_user(app, "kid")
check(kid is not None and not kid.is_admin, "added member is not admin")
check(kid.theme_preset == "sunny" and not kid.onboarded,
      "added member gets default theme and onboarding")
check(kid.session_token and len(kid.session_token) == 64,
      "added member has a session token")
r = add_member(mum, "KID")
check(r.status_code == 200
      and "That username is taken." in r.get_data(as_text=True)
      and user_count(app) == 2,
      "case-insensitive duplicate rejected")
r = add_member(mum, "Gran", "secret1", "secret2")
check(r.status_code == 200
      and "Passwords don&#39;t match." in r.get_data(as_text=True)
      and find_user(app, "gran") is None,
      "password mismatch rejected")
r = add_member(mum, "Gran", "123")
check(r.status_code == 200 and find_user(app, "gran") is None,
      "short password rejected")
r = add_member(mum, "a b")
check(r.status_code == 200 and user_count(app) == 2,
      "bad username rejected")

kid_c = login(app, "kid")
r = kid_c.get("/")
check(r.status_code == 302 and r.location.endswith("/welcome"),
      "new member sent to onboarding")
onboard(kid_c)
check(signed_in(kid_c), "new member reaches the feed")
r = add_member(kid_c, "Pal")
check(r.status_code == 403 and find_user(app, "pal") is None,
      "non-admin cannot add members")
check(kid_c.get("/admin").status_code == 403, "non-admin admin page 403")
r = add_member(anon, "Pal")
check(r.status_code == 302 and "/login" in r.location
      and find_user(app, "pal") is None,
      "signed-out visitor cannot add members")

# --- CSRF on the Add member form -----------------------------------
capp = make_app("csrf", csrf=True)
cc = capp.test_client()
tok = re.search(CSRF_RE, cc.get("/signup").get_data(as_text=True)).group(1)
cc.post("/signup", data={"csrf_token": tok, "username": "dad",
                         "password": "secret1",
                         "confirm_password": "secret1"})
tok = re.search(CSRF_RE, cc.get("/welcome").get_data(as_text=True)).group(1)
cc.post("/welcome", data={"csrf_token": tok, "avatar": "cat",
                          "theme_preset": "sunny"},
        content_type="multipart/form-data")
r = cc.post("/admin/members", data={"username": "nokey",
                                    "password": "secret1",
                                    "confirm_password": "secret1"})
check(find_user(capp, "nokey") is None,
      "Add member without CSRF token rejected")
tok = re.search(CSRF_RE, cc.get("/admin").get_data(as_text=True)).group(1)
r = cc.post("/admin/members", data={"csrf_token": tok, "username": "withkey",
                                    "password": "secret1",
                                    "confirm_password": "secret1"})
check(r.status_code == 302 and find_user(capp, "withkey") is not None,
      "Add member with CSRF token works")

# --- old cookie formats --------------------------------------------
kid = find_user(app, "kid")
old = app.test_client()
with old.session_transaction() as sess:
    sess["_user_id"] = str(kid.id)
    sess["_fresh"] = True
check(not signed_in(old), "old plain-id session rejected")
old = app.test_client()
with app.test_request_context():
    old.set_cookie("remember_token", encode_cookie(str(kid.id)))
check(not signed_in(old), "old plain-id remember cookie rejected")
for bad in ("", "abc", f"{kid.id}:", f"{kid.id}:wrong", f"x:{'0' * 64}",
            f"{kid.id}:{kid.session_token}:extra", "²:abc",
            "9" * 40 + ":abc"):
    bc = app.test_client()
    with bc.session_transaction() as sess:
        sess["_user_id"] = bad
    check(not signed_in(bc), f"malformed login id {bad!r} rejected")
good = app.test_client()
with good.session_transaction() as sess:
    sess["_user_id"] = f"{kid.id}:{kid.session_token}"
check(signed_in(good), "current id:token login id accepted")

# --- admin reset ends the member's sessions ------------------------
device_a = login(app, "kid")
check(signed_in(device_a), "kid signed in on device A")
r = mum.post(f"/admin/user/{kid.id}/reset-password",
             data={"new_password": "newpass1"})
check(r.status_code == 302, "admin reset password")
check(not signed_in(device_a), "device A signed out after admin reset")
check(not signed_in(kid_c), "older kid session signed out too")
check(signed_in(mum), "admin stays signed in after resetting someone")
device_b = login(app, "kid", "newpass1")
check(signed_in(device_b), "kid can log in with the new password")

# --- own password change keeps only this device --------------------
phone = login(app, "kid", "newpass1", remember=True)
tablet = login(app, "kid", "newpass1")
r = phone.post("/settings/password", data={
    "current_password": "newpass1",
    "new_password": "abcdef",
    "confirm_password": "abcdef",
})
check(r.status_code == 302, "own password change")
check(signed_in(phone), "changing device stays signed in")
check(not signed_in(tablet), "other device signed out after change")
check(not signed_in(device_b), "every other device signed out")
phone.delete_cookie("session")
check(signed_in(phone), "remember cookie refreshed on changing device")

# An admin resetting their own password stays signed in here only.
mum_tablet = login(app, "mum")
r = mum.post(f"/admin/user/{find_user(app, 'mum').id}/reset-password",
             data={"new_password": "mumpass1"})
check(r.status_code == 302 and signed_in(mum),
      "admin self-reset keeps this device")
check(not signed_in(mum_tablet), "admin self-reset ends other devices")

# --- remember me lasts 30 days -------------------------------------
check(app.config["REMEMBER_COOKIE_DURATION"] == timedelta(days=30),
      "REMEMBER_COOKIE_DURATION is 30 days")
check(app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax",
      "remember cookie is SameSite=Lax")
rem = login(app, "kid", "abcdef", remember=True)
cookie = rem.get_cookie("remember_token")
check(cookie is not None, "remember cookie set")
left = cookie.expires - datetime.now(timezone.utc)
check(timedelta(days=29, hours=23) < left <= timedelta(days=30),
      "remember cookie expires in 30 days")
rem.delete_cookie("session")
check(signed_in(rem), "remember cookie alone signs in")

# --- deleted users and id reuse ------------------------------------
add_member(mum, "Temp")
temp = find_user(app, "temp")
temp_c = login(app, "temp")
onboard(temp_c)
check(signed_in(temp_c), "temp signed in")
with app.app_context():
    highest = db.session.scalar(db.select(db.func.max(User.id)))
check(temp.id == highest, "temp has the highest id")
r = mum.post(f"/admin/user/{temp.id}/delete",
             data={"confirm": "yes", "confirm_name": "temp"})
check(r.status_code == 302 and find_user(app, "temp") is None,
      "temp deleted")
check(not signed_in(temp_c), "deleted user's session fails")
add_member(mum, "Newbie")
newbie = find_user(app, "newbie")
check(newbie.id > temp.id, "user id not reused after deletion")
check(newbie.session_token != temp.session_token, "fresh token for newbie")
check(not signed_in(temp_c), "old cookie does not match the new member")


# --- migration 1 on an old database --------------------------------
OLD_SCHEMA = """
CREATE TABLE user (
    id INTEGER NOT NULL, username VARCHAR(30) NOT NULL,
    password_hash VARCHAR(256) NOT NULL, display_name VARCHAR(60),
    bio VARCHAR(500), avatar VARCHAR(64) NOT NULL,
    is_admin BOOLEAN NOT NULL, onboarded BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL, theme_preset VARCHAR(20) NOT NULL,
    colour_primary VARCHAR(7), colour_accent VARCHAR(7),
    colour_bg VARCHAR(7), colour_card VARCHAR(7), colour_text VARCHAR(7),
    background_image VARCHAR(64), PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_user_username_lower ON user (lower(username));
CREATE TABLE post (
    id INTEGER NOT NULL, author_id INTEGER NOT NULL,
    title VARCHAR(120) NOT NULL, body TEXT NOT NULL, mood VARCHAR(10),
    visibility VARCHAR(10) NOT NULL, created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(author_id) REFERENCES user (id)
);
CREATE TABLE comment (
    id INTEGER NOT NULL, post_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL, body TEXT NOT NULL,
    created_at DATETIME NOT NULL, PRIMARY KEY (id),
    FOREIGN KEY(post_id) REFERENCES post (id),
    FOREIGN KEY(author_id) REFERENCES user (id)
);
CREATE TABLE reaction (
    id INTEGER NOT NULL, post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, kind VARCHAR(10) NOT NULL,
    created_at DATETIME NOT NULL, PRIMARY KEY (id),
    CONSTRAINT uq_reaction UNIQUE (post_id, user_id, kind),
    FOREIGN KEY(post_id) REFERENCES post (id),
    FOREIGN KEY(user_id) REFERENCES user (id)
);
"""


def build_old_db(path: Path) -> None:
    """Write a pre-migration database with linked rows.

    Users get ids 1, 2 and 5 (a gap, as after a deletion).

    Args:
        path: Where to create the database file.
    """
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    stamp = "2026-01-01 10:00:00"
    for uid, name, admin in ((1, "Mum", 1), (2, "Kid", 0), (5, "Gran", 0)):
        con.execute(
            "INSERT INTO user (id, username, password_hash, display_name, "
            "avatar, is_admin, onboarded, created_at, theme_preset, "
            "colour_primary) VALUES (?, ?, 'hash', ?, 'fox', ?, 1, ?, "
            "'sunny', '#123456')",
            (uid, name, f"{name} display", admin, stamp))
    con.execute(
        "INSERT INTO post VALUES (7, 2, 'Day', 'Fun', NULL, 'everyone', "
        "?, ?)", (stamp, stamp))
    con.execute(
        "INSERT INTO comment VALUES (3, 7, 5, 'Lovely', ?)", (stamp,))
    con.execute(
        "INSERT INTO reaction VALUES (4, 7, 1, 'heart', ?)", (stamp,))
    con.execute("PRAGMA user_version = 0")
    con.commit()
    con.close()


inst = Path(SCRATCH) / "migrate"
inst.mkdir()
db_path = inst / "family_blogger.db"
build_old_db(db_path)
mapp = create_app({"INSTANCE_DIR": str(inst), "TESTING": True,
                   "WTF_CSRF_ENABLED": False})
con = sqlite3.connect(db_path)
check(con.execute("PRAGMA user_version").fetchone()[0]
      == latest_version(),
      "migrated database is at the latest user_version")
users = con.execute(
    "SELECT id, username, display_name, is_admin, colour_primary, "
    "session_token FROM user ORDER BY id").fetchall()
check([(u[0], u[1]) for u in users] == [(1, "Mum"), (2, "Kid"),
                                        (5, "Gran")],
      "user rows and ids preserved")
check(users[0][2] == "Mum display" and users[0][3] == 1
      and users[0][4] == "#123456",
      "user column values preserved")
tokens = [u[5] for u in users]
check(all(t and re.fullmatch(r"[0-9a-f]{64}", t) for t in tokens),
      "session tokens populated with 64 hex characters")
check(len(set(tokens)) == len(tokens), "session tokens are unique")
check(con.execute("SELECT author_id FROM post WHERE id = 7").fetchone()
      == (2,), "post still linked to its author")
check(con.execute("SELECT post_id, author_id FROM comment WHERE id = 3")
      .fetchone() == (7, 5), "comment links preserved")
check(con.execute("SELECT post_id, user_id FROM reaction WHERE id = 4")
      .fetchone() == (7, 1), "reaction links preserved")
check(con.execute("PRAGMA foreign_key_check").fetchall() == [],
      "no broken foreign keys")
user_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                       "AND name='user'").fetchone()[0]
check("AUTOINCREMENT" in user_sql, "user table uses AUTOINCREMENT")
index_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='index' "
                        "AND name='ix_user_username_lower'").fetchone()
check(index_sql and "lower(username)" in index_sql[0]
      and "UNIQUE" in index_sql[0],
      "case-insensitive username index kept")
try:
    con.execute("INSERT INTO user (username, password_hash, avatar, "
                "is_admin, onboarded, created_at, theme_preset) VALUES "
                "('MUM', 'h', 'fox', 0, 0, '2026-01-01', 'sunny')")
    dup_allowed = True
except sqlite3.IntegrityError:
    dup_allowed = False
con.rollback()
check(not dup_allowed, "username index still enforced")
con.close()
backups = list(inst.glob("family_blogger.db.pre-migration-v0-*.db"))
check(len(backups) == 1, "pre-migration backup exists")
bcon = sqlite3.connect(backups[0])
check(bcon.execute("SELECT count(*) FROM user").fetchone()[0] == 3
      and bcon.execute("PRAGMA user_version").fetchone()[0] == 0,
      "backup holds the old data")
bcon.close()

# The migrated table matches what create_all() makes from the model.
fresh = Path(SCRATCH) / "fresh"
make_app("fresh")
fcon = sqlite3.connect(fresh / "family_blogger.db")


def columns(con: sqlite3.Connection) -> list[tuple]:
    """List the user table's columns.

    Args:
        con: Open database connection.

    Returns:
        ``PRAGMA table_info`` rows for ``user``.
    """
    return con.execute("PRAGMA table_info(user)").fetchall()


con = sqlite3.connect(db_path)
check(columns(con) == columns(fcon), "migrated columns match the model")
check(fcon.execute("PRAGMA user_version").fetchone()[0]
      == latest_version(),
      "fresh database stamped at the latest version")
fcon.close()

# Logging in works after migrating; new ids continue after the max.
with mapp.app_context():
    gran = db.session.get(User, 5)
    gran.set_password("secret1")
    db.session.commit()
mc = login(mapp, "gran")
check(signed_in(mc), "migrated user can log in")
with mapp.app_context():
    extra = User(username="extra", is_admin=False)
    extra.set_password("secret1")
    extra.apply_theme("sunny")
    db.session.add(extra)
    db.session.commit()
    check(extra.id == 6, "new ids follow the highest existing id")
    db.session.delete(extra)
    db.session.commit()
    again = User(username="again", is_admin=False)
    again.set_password("secret1")
    again.apply_theme("sunny")
    db.session.add(again)
    db.session.commit()
    check(again.id == 7, "deleted id not reused after migration")
con.close()

# Running the app again is a no-op: no new backup, same tokens.
create_app({"INSTANCE_DIR": str(inst), "TESTING": True})
con = sqlite3.connect(db_path)
check([r[0] for r in con.execute(
    "SELECT session_token FROM user WHERE id IN (1, 2, 5) ORDER BY id")]
    == tokens, "restart keeps tokens")
con.close()
check(len(list(inst.glob("*.pre-migration-*.db"))) == 1,
      "no extra backup on restart")

# The step itself skips a table that is already migrated.
with mapp.app_context():
    with db.engine.connect() as conn:
        before = conn.exec_driver_sql(
            "SELECT id, session_token FROM user ORDER BY id").fetchall()
        user_sessions_and_ids(conn)
        after = conn.exec_driver_sql(
            "SELECT id, session_token FROM user ORDER BY id").fetchall()
        conn.rollback()
check(before == after, "migration step is a no-op when already applied")

print(f"ALL {passed} CHECKS PASSED")
