"""Tests for the last review fixes.

Covers trusted reverse proxies and the shared-address warning (M1),
the trusted-device way out for a member paused from a shared address
(M1), pre-flight migration checks and de-duplicated backups (M2),
invisible characters in display names (L-a), the split between
"Clear failed log-ins" and "Unpause this device" (L-b), parallel
log-in attempts (L-c) and photo clean-up after any save error (L-f).
"""
import atexit
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from http.cookies import SimpleCookie
from datetime import timedelta
from pathlib import Path

from flask import Flask, request
from werkzeug.wrappers import Response
from flask.testing import FlaskClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
import config  # noqa: E402
from family_blogger import create_app, migrations  # noqa: E402
from family_blogger.forms import INVISIBLE_IN_NAME  # noqa: E402
from family_blogger.migrations import (  # noqa: E402
    Migration,
    MigrationError,
    migrate_database,
)
from family_blogger.models import (  # noqa: E402
    LoginFailure,
    LoginLock,
    Photo,
    User,
    db,
    name_key,
    utc_now,
)
from family_blogger.posts import routes as post_routes  # noqa: E402
from family_blogger.utils import is_safe_url  # noqa: E402
from family_blogger.utils import devices, proxy, throttle  # noqa: E402
from helpers import add_member, join_family  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="fb-final-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
INST = os.path.join(SCRATCH, "final_instance")
PROXY_NET = "10.9.0.0/30"

app = create_app({
    "INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False,
    "TRUSTED_PROXIES": [PROXY_NET]})
app.config["PROPAGATE_EXCEPTIONS"] = False
app.logger.disabled = True

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


def onboard(username: str) -> None:
    """Mark a member as onboarded so every page is reachable.

    Args:
        username: The member's username.
    """
    with app.app_context():
        User.find_by_username(username).onboarded = True
        db.session.commit()


def login(client: FlaskClient, username: str, password: str, ip: str,
          forwarded_for: str | None = None) -> object:
    """Post the log-in form from a given client address.

    Args:
        client: The test client.
        username: The username to type.
        password: The password to type.
        ip: The ``REMOTE_ADDR`` to send from.
        forwarded_for: An ``X-Forwarded-For`` header to send, if any.

    Returns:
        The test response.
    """
    headers = {}
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    return client.post(
        "/login", data={"username": username, "password": password},
        headers=headers, environ_overrides={"REMOTE_ADDR": ip})


def fail(username: str, ip: str, times: int) -> list[int]:
    """Type a wrong password several times from one address.

    Args:
        username: The username to type.
        ip: The address to send from.
        times: How many wrong tries.

    Returns:
        The status code of each try.
    """
    client = app.test_client()
    return [login(client, username, "wrong-pass", ip).status_code
            for _ in range(times)]


def throttled(username: str, ip: str) -> bool:
    """Ask the throttle whether a name and address are paused.

    Args:
        username: The username.
        ip: The address.

    Returns:
        ``True`` if a log-in would be refused.
    """
    with app.app_context():
        return throttle.is_throttled(username, ip)


def reset_throttle() -> None:
    """Forget every failure and pause between scenarios."""
    with app.app_context():
        db.session.execute(db.delete(LoginFailure))
        db.session.execute(db.delete(LoginLock))
        db.session.commit()


def failure_ips(username: str) -> list[str]:
    """List the addresses stored for a username's failures.

    Args:
        username: The lower-cased username.

    Returns:
        The ``ip`` column of each failure row, oldest first.
    """
    with app.app_context():
        return list(db.session.scalars(
            db.select(LoginFailure.ip)
            .where(LoginFailure.username == username)
            .order_by(LoginFailure.id)))


def spray(ip: str, count: int) -> None:
    """Make wrong tries for many made-up names from one address.

    Args:
        ip: The address.
        count: How many different names to try once each.
    """
    for n in range(count):
        fail(f"guess{n}", ip, 1)


mum = join_family(app, "mum")
onboard("mum")
for member in ("kid", "sib"):
    add_member(app, member)
    onboard(member)

# === M1: trusted proxies ===========================================
nets = proxy.parse_networks(["10.0.0.0/8", "192.168.1.2", "fd00::/8"])
check(proxy.is_trusted("10.1.2.3", nets), "address inside a CIDR trusted")
check(proxy.is_trusted("192.168.1.2", nets), "single address trusted")
check(not proxy.is_trusted("192.168.1.3", nets), "neighbour not trusted")
check(proxy.is_trusted("::ffff:10.0.0.1", nets),
      "IPv4-mapped IPv6 compared as IPv4")
check(proxy.is_trusted("fd00::5", nets), "IPv6 network trusted")
check(not proxy.is_trusted("not-an-ip", nets)
      and not proxy.is_trusted(None, nets), "garbage is never trusted")
try:
    proxy.parse_networks(["10.0.0.0/8", "proxy.local"])
    refused = False
except ValueError as exc:
    refused = "FB_TRUSTED_PROXIES" in str(exc)
check(refused, "a malformed entry stops the app with a clear message")

old_env = dict(os.environ)
os.environ["FB_TRUSTED_PROXIES"] = " 172.18.0.0/16, ,192.168.1.2 "
os.environ.pop("FB_TRUST_PROXY", None)
check(config.trusted_proxies_from_env() == ["172.18.0.0/16",
                                            "192.168.1.2"],
      "FB_TRUSTED_PROXIES is split on commas and trimmed")
check(not config.trust_any_proxy_from_env(), "legacy switch off")
os.environ["FB_TRUST_PROXY"] = "1"
check(config.trust_any_proxy_from_env(), "legacy FB_TRUST_PROXY=1 read")
os.environ.clear()
os.environ.update(old_env)
check(not config.trusted_proxies_from_env()
      or "FB_TRUSTED_PROXIES" in old_env, "empty by default")

echo = Flask("echo")


@echo.route("/who")
def who() -> str:
    """Echo what the app believes about the client.

    Returns:
        ``"<remote_addr> <scheme> <host>"``.
    """
    return f"{request.remote_addr} {request.scheme} {request.host}"


forwarded = {"X-Forwarded-For": "192.168.7.7",
             "X-Forwarded-Proto": "https",
             "X-Forwarded-Host": "blog.home"}
echo.wsgi_app = proxy.TrustedProxyMiddleware(echo.wsgi_app, ["10.9.0.1"])
seen = echo.test_client().get(
    "/who", headers=forwarded,
    environ_overrides={"REMOTE_ADDR": "10.9.0.1"}).get_data(as_text=True)
check(seen == "192.168.7.7 https localhost",
      f"a trusted proxy's address and scheme are used: {seen!r}")
seen = echo.test_client().get(
    "/who", headers=forwarded,
    environ_overrides={"REMOTE_ADDR": "10.9.0.2"}).get_data(as_text=True)
check(seen.startswith("10.9.0.2 http ") and "blog.home" not in seen,
      f"anyone else's forwarded headers are ignored: {seen!r}")
legacy = Flask("legacy")
legacy.add_url_rule("/who", view_func=who)
legacy.wsgi_app = proxy.TrustedProxyMiddleware(
    legacy.wsgi_app, [], trust_any=True)
seen = legacy.test_client().get(
    "/who", headers=forwarded,
    environ_overrides={"REMOTE_ADDR": "10.50.0.1"}).get_data(as_text=True)
check(seen.startswith("192.168.7.7 https"),
      "legacy FB_TRUST_PROXY=1 still trusts the immediate peer")

# Throttling sees the real device behind a trusted proxy ...
login(app.test_client(), "sib", "nope", "10.9.0.1", "192.168.7.7")
login(app.test_client(), "sib", "nope", "10.9.0.2", "192.168.7.8")
login(app.test_client(), "sib", "nope", "10.20.0.1", "192.168.7.9")
check(failure_ips("sib") == ["192.168.7.7", "192.168.7.8", "10.20.0.1"],
      f"forwarded address used only from trusted proxies: "
      f"{failure_ips('sib')}")
# ... so one child's wrong tries no longer pause the whole family.
reset_throttle()
for _ in range(throttle.MAX_FAILURES):
    login(app.test_client(), "mum", "nope", "10.9.0.1", "192.168.7.20")
r = login(app.test_client(), "mum", "secret1", "10.9.0.1", "192.168.7.21")
check(r.status_code == 302,
      "behind a trusted proxy Mum's own device is not paused")

# === M1: shared-address warning ====================================
reset_throttle()
html = mum.get("/admin").get_data(as_text=True)
check("Everyone seems to be connecting" not in html,
      "no warning without failures")
for n in range(throttle.SHARED_MIN_FAILURES - 1):
    fail(("kid", "sib", "mum", "gran")[n % 4], "172.18.0.1", 1)
html = mum.get("/admin").get_data(as_text=True)
check("Everyone seems to be connecting" not in html,
      "no warning below the minimum sample")
fail("sib", "172.18.0.1", 1)
html = mum.get("/admin").get_data(as_text=True)
check("Everyone seems to be connecting from the same address "
      "(172.18.0.1). Login pauses then affect the whole family — see "
      "“Using your own domain and HTTPS” in the README and set "
      "FB_TRUSTED_PROXIES." in html,
      "warning shown when ≥80% of failures share one address")
fail("sib", "10.30.0.1", 1)
fail("kid", "10.30.0.2", 1)
fail("sib", "10.30.0.3", 1)
html = mum.get("/admin").get_data(as_text=True)
check("Everyone seems to be connecting" not in html,
      "no warning once under 80% come from one address")
reset_throttle()
fail("kid", "172.18.0.1", throttle.SHARED_MIN_FAILURES + 5)
with app.app_context():
    check(throttle.shared_address() is None,
          "one child's typos on one device are not a shared address")

# === M1: the sole admin always has a way out ========================
reset_throttle()
SHARED = "172.18.0.1"
mum_laptop = app.test_client()
r = login(mum_laptop, "mum", "secret1", SHARED)
cookie_header = " ".join(r.headers.getlist("Set-Cookie"))
check(r.status_code == 302 and "fb_device=" in cookie_header
      and "HttpOnly" in cookie_header and "SameSite=Lax" in cookie_header,
      "a good log-in marks the browser as a trusted device")
mum_laptop.post("/logout")
kid_tablet = app.test_client()
login(kid_tablet, "kid", "secret1", SHARED)
kid_tablet.post("/logout")
reset_throttle()
# A child at the same (shared) address pauses Mum's name there and
# then the whole address.
check(fail("mum", SHARED, throttle.MAX_FAILURES)
      == [200] * throttle.MAX_FAILURES, "five wrong guesses for Mum")
spray(SHARED, throttle.IP_MAX_FAILURES)
check(throttled("mum", SHARED) and throttled("anyone", SHARED),
      "Mum's name and the whole address are paused")
r = login(app.test_client(), "mum", "secret1", SHARED)
check(r.status_code == 429, "a new browser is still refused")
r = login(kid_tablet, "mum", "secret1", SHARED)
check(r.status_code == 429,
      "the kid's device cookie only covers the kid, not Mum")
forged = app.test_client()
forged.set_cookie("fb_device", "eyJ4IjoieSJ9.forged.sig")
check(login(forged, "mum", "secret1", SHARED).status_code == 429,
      "a forged device cookie is ignored")
r = login(mum_laptop, "mum", "secret1", SHARED)
check(r.status_code == 302,
      "Mum logs in on her own browser despite the shared-address pauses")
mum_laptop.post("/logout")

# The device budget is bounded: a stolen cookie gives at most
# MAX_FAILURES extra guesses, then the token is never trusted again.
stolen = app.test_client()
stolen.set_cookie("fb_device", mum_laptop.get_cookie("fb_device").value)
codes = [login(stolen, "mum", "guess", SHARED).status_code
         for _ in range(throttle.MAX_FAILURES)]
check(codes == [200] * throttle.MAX_FAILURES,
      f"the token's own budget is {throttle.MAX_FAILURES}: {codes}")
r = login(stolen, "mum", "secret1", SHARED)
check(r.status_code == 429,
      "after that the browser falls back to the shared-address pause")
check(login(mum_laptop, "mum", "secret1", SHARED).status_code == 429,
      "the spent token is revoked for its real owner too")
r = login(app.test_client(), "mum", "secret1", "192.168.50.5")
check(r.status_code == 302, "Mum is fine from any other address")
with app.app_context():
    dev_locks = db.session.scalar(
        db.select(db.func.count(LoginLock.id))
        .where(LoginLock.ip.like(devices.PREFIX + "%")))
    check(dev_locks == 1, "one device-key pause recorded")
    # The revocation outlives the 7-day prune.
    db.session.execute(
        db.update(LoginLock)
        .where(LoginLock.ip.like(devices.PREFIX + "%"))
        .values(locked_until=utc_now() - throttle.KEEP_FOR
                - timedelta(days=1)))
    db.session.commit()
    throttle.prune()
    db.session.commit()
    key = db.session.scalar(
        db.select(LoginLock.ip)
        .where(LoginLock.ip.like(devices.PREFIX + "%")))
    check(key is not None and throttle.device_revoked(key),
          "a revoked device key survives the weekly prune")
# Logging in again on that browser (from elsewhere) trusts it afresh.
r = login(mum_laptop, "mum", "secret1", "192.168.50.6")
mum_laptop.post("/logout")
check(r.status_code == 302
      and login(mum_laptop, "mum", "secret1", SHARED).status_code == 302,
      "a fresh token after a good log-in restores the way out")

# === L-b: clear failed log-ins vs unpause this device ===============
reset_throttle()
DEVICE = "10.5.0.1"
fail("sib", DEVICE, throttle.MAX_FAILURES)
spray(DEVICE, throttle.IP_MAX_FAILURES)
r = mum.post("/admin/login-failures/clear", data={"username": "sib"})
check(r.status_code == 302, "clear failed log-ins done")
with app.app_context():
    now = utc_now()
    pair_running = db.session.scalar(
        db.select(db.func.count(LoginLock.id))
        .where(LoginLock.scope == "pair", LoginLock.username == "sib",
               LoginLock.locked_until > now))
check(pair_running == 0, "the name's pause is lifted")
check(throttled("sib", DEVICE) and throttled("someone", DEVICE),
      "the whole-device pause is NOT lifted by clearing a name")
html = mum.get("/admin").get_data(as_text=True)
check('id="paused-devices"' in html and "Unpause this device" in html
      and f'name="ip" value="{DEVICE}"' in html,
      "admin page lists the paused device with an unpause button")
form_start = html.index("/admin/login-pauses/device")
check('name="csrf_token"' in html[form_start:form_start + 300],
      "the unpause form carries a CSRF token")
kid = app.test_client()
login(kid, "kid", "secret1", "10.5.0.9")
check(kid.post("/admin/login-pauses/device",
               data={"ip": DEVICE}).status_code == 403,
      "children cannot unpause devices")
check(mum.get("/admin/login-pauses/device").status_code == 405,
      "unpausing is POST only")
r = mum.post("/admin/login-pauses/device", data={"ip": DEVICE},
             follow_redirects=True)
check(r.status_code == 200
      and f"The device at {DEVICE} can try logging in again"
      in r.get_data(as_text=True), "unpausing shows a calm message")
check(not throttled("someone", DEVICE), "the device pause is lifted")
check(not throttled("sib", DEVICE), "sib's name was already cleared")
check('id="paused-devices"' not in mum.get("/admin").get_data(
    as_text=True), "nothing left to unpause")
# Unpausing a device leaves name pauses alone.
reset_throttle()
fail("kid", "10.5.0.2", throttle.MAX_FAILURES)
spray("10.5.0.2", throttle.IP_MAX_FAILURES)
mum.post("/admin/login-pauses/device", data={"ip": "10.5.0.2"})
check(throttled("kid", "10.5.0.2") and not throttled("x", "10.5.0.2"),
      "unpausing the device keeps the kid's name paused there")

# === L-c: parallel attempts cannot exceed the limit ================
reset_throttle()
with app.test_request_context("/"):
    ids = [throttle.begin_attempt("kid", "10.6.0.1")
           for _ in range(throttle.MAX_FAILURES + 3)]
check(all(ids[:throttle.MAX_FAILURES])
      and ids[throttle.MAX_FAILURES:] == [None] * 3,
      f"in-flight attempts count before any finishes: {ids}")
with app.app_context():
    rows = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "kid"))
check(rows == throttle.MAX_FAILURES, "refused attempts leave no rows")
with app.test_request_context("/"):
    throttle.attempt_succeeded(ids[0], "kid")
    db.session.commit()
    check(throttle.begin_attempt("kid", "10.6.0.1") is not None,
          "a correct password frees its reserved slot")

reset_throttle()
THREADS = 12
barrier = threading.Barrier(THREADS)
results = []
lock = threading.Lock()


def racer() -> None:
    """Send one wrong password for kid at the same moment as others."""
    client = app.test_client()
    barrier.wait()
    code = login(client, "kid", "wrong-pass", "10.6.0.2").status_code
    with lock:
        results.append(code)


workers = [threading.Thread(target=racer) for _ in range(THREADS)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
checked = results.count(200)
check(len(results) == THREADS and set(results) <= {200, 429},
      f"every parallel request answered: {results}")
check(checked <= throttle.MAX_FAILURES + 1,
      f"{THREADS} parallel guesses checked only {checked} passwords")
check(throttled("kid", "10.6.0.2"), "the name is paused afterwards")
with app.app_context():
    rows = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "kid"))
check(rows == checked, "only checked passwords are stored as failures")

# === L-a: invisible characters in display names ====================
PROBES = ["Mu​m", "‍Mum", "M­um", "Mum⠀",
          "Mumㅤ", "Mum️"]
sib = app.test_client()
login(sib, "sib", "secret1", "10.7.0.1")
for probe in PROBES:
    check(name_key(probe) == "mum", f"name_key strips {probe!r}")
    r = sib.post("/settings/profile",
                 data={"display_name": probe, "bio": ""})
    check(r.status_code == 200
          and INVISIBLE_IN_NAME in r.get_data(as_text=True),
          f"settings refuses invisible characters in {probe!r}")
check(name_key("Mum\U000e0100") == "mum"
      and name_key("Muᅟmᅠﾠ͏") == "mum",
      "other fillers and supplementary variation selectors stripped")
with app.app_context():
    check(User.find_by_username("sib").display_name is None,
          "refused names are not saved")
r = sib.post("/settings/profile",
             data={"display_name": "Мum", "bio": ""})
check(r.status_code == 302, "Cyrillic letters are accepted")
add_member(app, "newbie")
newbie = app.test_client()
login(newbie, "newbie", "secret1", "10.7.0.2")
r = newbie.post("/welcome", data={
    "display_name": "Gr​an", "bio": "", "theme_preset": "sunny",
    "avatar": "fox"})
check(r.status_code == 200
      and INVISIBLE_IN_NAME in r.get_data(as_text=True),
      "onboarding refuses invisible characters too")

# === L-f: photos saved before any error are removed ================
upload_dir = Path(app.config["UPLOAD_FOLDER"])


def jpeg() -> io.BytesIO:
    """Make a small JPEG upload.

    Returns:
        The image bytes as a stream.
    """
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 120, 60)).save(buf, "JPEG")
    buf.seek(0)
    return buf


real_save = post_routes.save_image
calls = []


def flaky_save(file_storage: object, kind: str = "post") -> str:
    """Save the first photo for real, then fail like a full disk.

    Args:
        file_storage: The uploaded file.
        kind: The upload kind.

    Returns:
        The stored filename (first call only).

    Raises:
        OSError: On the second call.
    """
    calls.append(kind)
    if len(calls) == 2:
        raise OSError(28, "No space left on device")
    return real_save(file_storage, kind)


before = set(os.listdir(upload_dir))
post_routes.save_image = flaky_save
try:
    r = mum.post("/post/new", data={
        "title": "Beach", "body": "", "mood": "", "visibility": "everyone",
        "photos": [(jpeg(), "a.jpg"), (jpeg(), "b.jpg")]},
        content_type="multipart/form-data")
finally:
    post_routes.save_image = real_save
check(len(calls) == 2, "the second photo's save was attempted")
check(r.status_code == 500, f"the error still surfaces ({r.status_code})")
check(set(os.listdir(upload_dir)) == before,
      "the first photo was deleted after the OSError")
with app.app_context():
    check(db.session.scalar(db.select(db.func.count(Photo.id))) == 0,
          "no photo rows were stored")

# === M2: pre-flight checks and backups =============================
MIG = Path(SCRATCH) / "mig"


def run_migrations(directory: Path, steps: list | None = None) -> None:
    """Run ``migrate_database`` on ``directory``'s database.

    Args:
        directory: Folder holding ``family_blogger.db``.
        steps: Migration steps, or ``None`` for the real ones.
    """
    mig_app = Flask(__name__)
    mig_app.config.update(
        SQLALCHEMY_DATABASE_URI=(
            f"sqlite:///{directory / 'family_blogger.db'}"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False)
    migrations.db.init_app(mig_app)
    with mig_app.app_context():
        try:
            migrate_database(mig_app, steps)
        finally:
            migrations.db.engine.dispose()


def snapshot(path: Path) -> tuple:
    """Describe a database's schema, rows and version.

    Args:
        path: The database file.

    Returns:
        ``(user_version, schema rows, user rows)``.
    """
    con = sqlite3.connect(path)
    try:
        return (con.execute("PRAGMA user_version").fetchone()[0],
                con.execute("SELECT type, name, sql FROM sqlite_master "
                            "ORDER BY name").fetchall(),
                con.execute("SELECT * FROM user ORDER BY id").fetchall())
    finally:
        con.close()


def backups(directory: Path) -> list[Path]:
    """List the pre-migration backups in a folder.

    Args:
        directory: The folder.

    Returns:
        The backup files.
    """
    return sorted(directory.glob("*.pre-migration-*.db"))


# A legacy database the real migrations must refuse (Kid and kid).
MIG.mkdir()
legacy_db = MIG / "family_blogger.db"
con = sqlite3.connect(legacy_db)
con.executescript(
    "CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(30),"
    " password_hash VARCHAR(256));"
    "INSERT INTO user VALUES (1, 'Kid', 'x'), (2, 'kid', 'y');")
con.commit()
con.close()
before = snapshot(legacy_db)
outcomes = []
for _ in range(3):
    try:
        run_migrations(MIG)
        outcomes.append("ran")
    except MigrationError as exc:
        outcomes.append("refused" if "capital letters" in str(exc)
                        else str(exc))
check(outcomes == ["refused"] * 3, f"refused every time: {outcomes}")
check(len(backups(MIG)) <= 1,
      f"at most one backup after 3 refusals: {backups(MIG)}")
check(not backups(MIG), "the pre-flight check refuses before backing up")
check(snapshot(legacy_db) == before, "the refused database is untouched")


def explode(conn: object) -> None:
    """Test step: change data, then fail.

    Args:
        conn: Connection inside the migration transaction.

    Raises:
        RuntimeError: Always.
    """
    conn.exec_driver_sql("DELETE FROM person")
    raise RuntimeError("boom")


def refuse_check(conn: object) -> None:
    """Test pre-flight check that always refuses.

    Args:
        conn: Open connection.

    Raises:
        MigrationError: Always.
    """
    raise MigrationError("not today")


CRASH = Path(SCRATCH) / "crash"
CRASH.mkdir()
con = sqlite3.connect(CRASH / "family_blogger.db")
con.executescript("CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);"
                  "INSERT INTO person VALUES (1, 'Ann');")
con.commit()
con.close()
for _ in range(3):
    try:
        run_migrations(CRASH, [Migration(1, "explode", explode)])
    except RuntimeError:
        pass
check(len(backups(CRASH)) == 1,
      f"a step failing 3 times leaves one backup: {backups(CRASH)}")
con = sqlite3.connect(CRASH / "family_blogger.db")
con.execute("INSERT INTO person VALUES (2, 'Bo')")
con.commit()
con.close()
try:
    run_migrations(CRASH, [Migration(1, "explode", explode)])
except RuntimeError:
    pass
check(len(backups(CRASH)) == 2,
      "changed data gets a new backup (the old one is never reused)")
check(not list(CRASH.glob("*.partial")), "no partial copies left")
con = sqlite3.connect(CRASH / "family_blogger.db")
con.execute("INSERT INTO person VALUES (3, 'Cy')")
con.commit()
con.close()
try:
    run_migrations(CRASH, [Migration(1, "noop", explode, refuse_check)])
    refused = False
except MigrationError:
    refused = True
check(refused and len(backups(CRASH)) == 2,
      "a step's own check runs before any backup")

# === Review fixes: device trust, forwarded host, mapped nets, copy ===
# Device trust ends when the session token rotates (admin reset).
reset_throttle()
SHARED2 = "172.18.0.5"
sib_tab = app.test_client()
login(sib_tab, "sib", "secret1", SHARED2)
sib_tab.post("/logout")
fail("sib", SHARED2, throttle.MAX_FAILURES)
spray(SHARED2, throttle.IP_MAX_FAILURES)
check(throttled("sib", SHARED2), "sib is paused at the shared address")
r = login(sib_tab, "sib", "secret1", SHARED2)
check(r.status_code == 302, "the trusted device still gets through")
sib_tab.post("/logout")
admin_tab = app.test_client()
login(admin_tab, "mum", "secret1", "192.168.60.1")
with app.app_context():
    sib_id = User.find_by_username("sib").id
r = admin_tab.post(f"/admin/user/{sib_id}/reset-password",
                   data={"new_password": "fresh-pass1"})
check(r.status_code == 302, "admin resets sib's password")
reset_throttle()
fail("sib", SHARED2, throttle.MAX_FAILURES)
spray(SHARED2, throttle.IP_MAX_FAILURES)
r = login(sib_tab, "sib", "fresh-pass1", SHARED2)
check(r.status_code == 429,
      "the old device cookie gives no extra guesses after a reset")

# Entries age out one by one; old-shape entries are expired.
with app.app_context():
    gen = devices._generation(User.find_by_username("kid"))
    kid_id = User.find_by_username("kid").id
    now = int(time.time())
    day = 86400
    signed = devices._serializer(app).dumps({
        "101": ["a" * 24, gen, now - 100 * day],
        "102": ["b" * 24, gen, now - 1 * day],
        "103": "c" * 24,
        "104": ["d" * 24, gen, now - 89 * day]})
with app.test_request_context(headers={"Cookie": f"fb_device={signed}"}):
    kept = sorted(devices._read_cookie())
check(kept == ["102", "104"], f"entries age out individually: {kept}")

signed = devices._serializer(app).dumps({
    "102": ["b" * 24, gen, now - 80 * day]})
with app.test_request_context(headers={"Cookie": f"fb_device={signed}"}):
    with app.app_context():
        resp = Response()
        devices.remember_device(resp, User.find_by_username("kid"))
    jar = SimpleCookie()
    jar.load(resp.headers["Set-Cookie"])
    data = devices._serializer(app).loads(jar["fb_device"].value)
check(data["102"][2] == now - 80 * day
      and data[str(kid_id)][2] >= now,
      "only the logging-in member's entry gets a fresh timestamp")

# X-Forwarded-Host is never honoured, even from a trusted peer.
evil = {"X-Forwarded-Host": "evil.example", "X-Forwarded-Port": "99"}
seen = echo.test_client().get(
    "/who", headers=evil,
    environ_overrides={"REMOTE_ADDR": "10.9.0.1"}).get_data(as_text=True)
check(seen.endswith(" localhost"), f"forwarded host ignored: {seen!r}")


def safe_probe() -> str:
    """Report whether a foreign URL is judged safe.

    Returns:
        ``"True"`` or ``"False"``.
    """
    return str(is_safe_url("http://evil.example/"))


probe = Flask("probe")
probe.add_url_rule("/safe", view_func=safe_probe)
probe.wsgi_app = proxy.TrustedProxyMiddleware(probe.wsgi_app, ["10.9.0.1"])
seen = probe.test_client().get(
    "/safe", headers=evil,
    environ_overrides={"REMOTE_ADDR": "10.9.0.1"}).get_data(as_text=True)
check(seen == "False", "a forwarded host does not make a URL safe")

# IPv4-mapped IPv6 networks become IPv4 networks.
nets = proxy.parse_networks(["::ffff:10.8.0.0/112"])
check(str(nets[0]) == "10.8.0.0/16", f"mapped net unmapped: {nets}")
check(proxy.is_trusted("::ffff:10.8.0.2", nets)
      and proxy.is_trusted("10.8.0.2", nets)
      and not proxy.is_trusted("10.9.0.2", nets),
      "mapped network trusts both spellings of the peer")
for bad in ("::/0", "::ffff:0:0/80"):
    try:
        proxy.parse_networks([bad])
        refused = False
    except ValueError as exc:
        refused = "ambiguous" in str(exc)
    check(refused, f"ambiguous network {bad} is refused")

# A failed backup copy leaves no .partial file and the DB untouched.
BROKEN = Path(SCRATCH) / "broken-copy"
BROKEN.mkdir()
con = sqlite3.connect(BROKEN / "family_blogger.db")
con.execute("CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT)")
con.execute("INSERT INTO person VALUES (1, 'Al')")
con.commit()
con.close()
before = (BROKEN / "family_blogger.db").read_bytes()
real_copy = migrations._copy_database


def failing_copy(db_path: str, dest: str) -> None:
    """Write part of a copy, then fail like a full disk.

    Args:
        db_path: Path of the live database file.
        dest: Path of the copy to write.

    Raises:
        OSError: Always.
    """
    Path(dest).write_bytes(b"half")
    raise OSError("disk full")


migrations._copy_database = failing_copy
try:
    migrations._backup_database(str(BROKEN / "family_blogger.db"), 0)
    raised = False
except OSError:
    raised = True
finally:
    migrations._copy_database = real_copy
check(raised, "the copy error is re-raised")
check(not list(BROKEN.glob("*.partial")), "no .partial file remains")
check((BROKEN / "family_blogger.db").read_bytes() == before,
      "the database is untouched")

print(f"ALL {passed} CHECKS PASSED")
