"""Gentle log-in throttling, stored in the database.

Every failed log-in is recorded with the username tried and the
address it came from. Three limits slow down password guessing
without letting one child lock someone else out:

* **One name from one device** (``"pair"``): ``MAX_FAILURES`` wrong
  passwords for a username from one address within ``WINDOW`` pause
  that username *on that address only* for ``LOCKOUT``. The same
  person on another device is not affected.
* **Many names from one device** (``"ip"``): ``IP_MAX_FAILURES``
  failures from one address within ``IP_WINDOW``, whatever names were
  typed, pause that address for ``IP_LOCKOUT``. This catches
  guessing across the family, not ordinary typos.
* **One name from many devices** (``"name"``): ``NAME_MAX_FAILURES``
  failures for a username within ``NAME_WINDOW`` pause that username
  everywhere for ``NAME_LOCKOUT``. Each address adds at most
  ``MAX_FAILURES`` to this count, so it takes at least
  ``NAME_MAX_FAILURES // MAX_FAILURES`` different devices: one
  device (even a shared family tablet) can never trigger it.

A lock lasts a fixed time from the failure that caused it. Attempts
during a lock are refused before the password is checked and are not
recorded, and only failures *after* a lock ends count towards the
next one, so a lock cannot be kept alive with one wrong try at a
time.

**Parallel requests** cannot slip past a limit: ``begin_attempt``
stores the attempt as a failure *before* the password is checked and
then counts again, so each in-flight attempt sees every earlier one.
An attempt that would go over a limit is refused (and its row
removed); a correct password removes the row again
(``attempt_succeeded``). At most ``MAX_FAILURES`` passwords are
checked per lock window, however many requests arrive at once.

**Trusted devices** (``utils.devices``): a browser where a member
has logged in before is counted under its own ``"dev:<token>"`` key
for that member, with only the ``"pair"`` limit. So a pause caused
from a shared address (e.g. a whole family behind one proxy) cannot
lock that member out of their own browser. The first pause of a
device key also revokes it for good (it is kept for
``DEVICE_KEEP_FOR``), so each token adds at most ``MAX_FAILURES``
guesses.

A successful log-in clears that username's failures from that
address (or device). An admin can clear a username's failures and
lift its name pauses from the admin page, and resetting a member's
password does the same. Whole-device pauses are lifted separately
("Unpause this device"). Cleared rows stay (admins still see them)
until they are older than ``KEEP_FOR``.

``client_ip`` gives the address used for all of this: the real
client when a trusted proxy forwarded the request (``utils.proxy``).

No extra dependency is needed, and the counts survive restarts and
work across several server processes.
"""
from collections import Counter
from datetime import datetime, timedelta

from flask import request
from sqlalchemy import func

from ..models import LoginFailure, LoginLock, db, utc_now
from .devices import PREFIX as DEVICE_PREFIX
from .devices import TRUST_FOR as DEVICE_KEEP_FOR

MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=5)

IP_MAX_FAILURES = 20
IP_WINDOW = timedelta(minutes=15)
IP_LOCKOUT = timedelta(minutes=15)

NAME_MAX_FAILURES = 30
NAME_WINDOW = timedelta(hours=1)
NAME_LOCKOUT = timedelta(minutes=15)

KEEP_FOR = timedelta(days=7)

# The admin page warns that everyone seems to share one address when
# at least SHARED_SHARE of the last SHARED_SAMPLE failures (and at
# least SHARED_MIN_FAILURES of them, for two or more usernames) came
# from it.
SHARED_SAMPLE = 50
SHARED_SHARE = 0.8
SHARED_MIN_FAILURES = 10

THROTTLED_MESSAGE = (
    "Too many tries — please wait a few minutes or ask a grown-up.")


def _normalise(username: str | None) -> str:
    """Turn a typed username into the key used for counting.

    Args:
        username: The username as typed, possibly ``None``.

    Returns:
        The trimmed, lower-cased username, at most 80 characters.
    """
    return (username or "").strip().lower()[:80]


def client_ip() -> str:
    """Return the current request's client address for throttling.

    This is ``request.remote_addr`` after ``utils.proxy`` has
    applied any trusted proxy's ``X-Forwarded-For``, so behind a
    proxy listed in ``FB_TRUSTED_PROXIES`` it is the real device.

    Returns:
        The tidied address, or ``"unknown"``.
    """
    return _client_ip(request.remote_addr)


def _is_device(addr: str) -> bool:
    """Say whether a throttle address is a trusted-device key.

    Args:
        addr: A tidied address or ``"dev:<token>"`` key.

    Returns:
        ``True`` for device keys.
    """
    return addr.startswith(DEVICE_PREFIX)


def _scopes(addr: str) -> tuple[str, ...]:
    """List the limits that apply to an address or device key.

    Args:
        addr: A tidied address or device key.

    Returns:
        Only ``("pair",)`` for a device key, otherwise all three.
    """
    return ("pair",) if _is_device(addr) else ("pair", "ip", "name")


def _client_ip(ip: str | None) -> str:
    """Tidy the client address for storage.

    Args:
        ip: ``request.remote_addr``, possibly ``None``.

    Returns:
        The address, or ``"unknown"``, at most 45 characters.
    """
    return (ip or "unknown")[:45]


def _lock_filter(scope: str, name: str, ip: str) -> list:
    """Build the conditions that pick one lock key.

    Args:
        scope: ``"pair"``, ``"ip"`` or ``"name"``.
        name: The normalised username.
        ip: The tidied address.

    Returns:
        SQLAlchemy conditions on ``LoginLock`` for that key.
    """
    conds = [LoginLock.scope == scope]
    if scope in ("pair", "name"):
        conds.append(LoginLock.username == name)
    if scope in ("pair", "ip"):
        conds.append(LoginLock.ip == ip)
    return conds


def _failure_filter(scope: str, name: str, ip: str) -> list:
    """Build the conditions that pick the failures for one key.

    Args:
        scope: ``"pair"``, ``"ip"`` or ``"name"``.
        name: The normalised username.
        ip: The tidied address.

    Returns:
        SQLAlchemy conditions on uncleared ``LoginFailure`` rows.
    """
    conds = [LoginFailure.cleared.is_(False)]
    if scope in ("pair", "name"):
        conds.append(LoginFailure.username == name)
    if scope in ("pair", "ip"):
        conds.append(LoginFailure.ip == ip)
    return conds


def _counting_since(scope: str, name: str, ip: str,
                    window: timedelta, now: datetime) -> datetime:
    """Find where counting starts for one key.

    Args:
        scope: ``"pair"``, ``"ip"`` or ``"name"``.
        name: The normalised username.
        ip: The tidied address.
        window: How far back failures count.
        now: The current UTC time.

    Returns:
        The later of the window's start and the end of the key's
        last lock, so failures that caused a lock never count again.
    """
    last_end = db.session.scalar(
        db.select(func.max(LoginLock.locked_until))
        .where(*_lock_filter(scope, name, ip)))
    start = now - window
    if last_end is not None and last_end > start:
        return last_end
    return start


def _failure_count(scope: str, name: str, ip: str,
                   since: datetime, upto_id: int | None = None) -> int:
    """Count the uncleared failures for one key since a time.

    For ``"name"`` each address adds at most ``MAX_FAILURES``, so one
    device cannot reach the many-devices limit on its own.

    Args:
        scope: ``"pair"``, ``"ip"`` or ``"name"``.
        name: The normalised username.
        ip: The tidied address.
        since: Only failures at or after this time count.
        upto_id: Only count rows stored up to this one (attempts
            that started earlier), or ``None`` for all.

    Returns:
        The number of failures that count.
    """
    conds = _failure_filter(scope, name, ip)
    conds.append(LoginFailure.created_at >= since)
    if upto_id is not None:
        conds.append(LoginFailure.id <= upto_id)
    if scope == "name":
        per_ip = db.session.execute(
            db.select(func.count(LoginFailure.id))
            .where(*conds).group_by(LoginFailure.ip)).scalars()
        return sum(min(count, MAX_FAILURES) for count in per_ip)
    return db.session.scalar(
        db.select(func.count(LoginFailure.id)).where(*conds))


# scope -> (threshold, window, lock length)
_LIMITS = {
    "pair": (MAX_FAILURES, WINDOW, LOCKOUT),
    "ip": (IP_MAX_FAILURES, IP_WINDOW, IP_LOCKOUT),
    "name": (NAME_MAX_FAILURES, NAME_WINDOW, NAME_LOCKOUT),
}


def _is_locked(scope: str, name: str, ip: str, now: datetime) -> bool:
    """Say whether one key has a lock that has not ended yet.

    Args:
        scope: ``"pair"``, ``"ip"`` or ``"name"``.
        name: The normalised username.
        ip: The tidied address.
        now: The current UTC time.

    Returns:
        ``True`` while a lock for that key is running.
    """
    return db.session.scalar(
        db.select(func.count(LoginLock.id))
        .where(*_lock_filter(scope, name, ip),
               LoginLock.locked_until > now)) > 0


def device_revoked(key: str) -> bool:
    """Say whether a trusted-device key has ever been paused.

    Args:
        key: A ``"dev:<token>"`` key.

    Returns:
        ``True`` once the key has been paused; it is then never
        trusted again (until the member logs in on that browser and
        gets a new token).
    """
    return db.session.scalar(
        db.select(func.count(LoginLock.id))
        .where(LoginLock.scope == "pair", LoginLock.ip == key)) > 0


def is_throttled(username: str | None, ip: str | None) -> bool:
    """Say whether a log-in attempt should be refused for now.

    Args:
        username: The username being tried.
        ip: The client address (see ``client_ip``) or a device key.

    Returns:
        ``True`` if this username on this address, this address, or
        this username everywhere is paused. For a device key only
        its own pause counts.
    """
    name, addr, now = _normalise(username), _client_ip(ip), utc_now()
    return any(_is_locked(scope, name, addr, now)
               for scope in _scopes(addr))


def _over_limit(name: str, addr: str, now: datetime,
                upto_id: int) -> bool:
    """Say whether an attempt would take a key over its limit.

    Only attempts stored before it (and itself) count, so among
    parallel attempts the earliest ones always go ahead and the
    total never passes the threshold.

    Args:
        name: The normalised username.
        addr: The tidied address or device key.
        now: The current UTC time.
        upto_id: The attempt's own row id.

    Returns:
        ``True`` if any limit's count is above its threshold.
    """
    for scope in _scopes(addr):
        limit, window, _length = _LIMITS[scope]
        since = _counting_since(scope, name, addr, window, now)
        if _failure_count(scope, name, addr, since, upto_id) > limit:
            return True
    return False


def _apply_locks(name: str, addr: str, now: datetime) -> None:
    """Start any lock that the stored failures have now earned.

    Args:
        name: The normalised username.
        addr: The tidied address or device key.
        now: The current UTC time.
    """
    for scope in _scopes(addr):
        limit, window, length = _LIMITS[scope]
        if _is_locked(scope, name, addr, now):
            continue
        since = _counting_since(scope, name, addr, window, now)
        if _failure_count(scope, name, addr, since) >= limit:
            db.session.add(LoginLock(
                scope=scope,
                username=name if scope != "ip" else "",
                ip=addr if scope != "name" else "",
                started_at=now,
                locked_until=now + length))


def begin_attempt(username: str | None, ip: str | None,
                  device: str | None = None) -> int | None:
    """Reserve a log-in attempt before the password is checked.

    The attempt is stored (and committed) as a failure first, then
    the limits are counted again including it. If parallel requests
    together would go over a limit, the late ones are refused, so no
    more than the threshold of passwords are ever checked. Commits.

    Args:
        username: The username being tried.
        ip: The client address (see ``client_ip``).
        device: This browser's ``"dev:<token>"`` key for the member,
            from ``utils.devices.device_key``, or ``None``. It is
            used instead of ``ip`` unless it has been revoked.

    Returns:
        The id of the reserved attempt, to pass to
        ``attempt_failed`` or ``attempt_succeeded``; ``None`` if the
        attempt must be refused.
    """
    name, now = _normalise(username), utc_now()
    addr = _client_ip(ip)
    if device and not device_revoked(device):
        addr = device
    if is_throttled(name, addr):
        return None
    row = LoginFailure(username=name, ip=addr, created_at=now)
    db.session.add(row)
    db.session.commit()
    if is_throttled(name, addr) or _over_limit(name, addr, now, row.id):
        db.session.delete(row)
        db.session.commit()
        return None
    return row.id


def attempt_failed(attempt_id: int) -> None:
    """Keep a reserved attempt as a failure and start any lock.

    Commits.

    Args:
        attempt_id: The id from ``begin_attempt``.
    """
    row = db.session.get(LoginFailure, attempt_id)
    if row is not None:
        _apply_locks(row.username, row.ip, utc_now())
    prune()
    db.session.commit()


def attempt_succeeded(attempt_id: int, username: str) -> None:
    """Forget a reserved attempt after a correct password.

    Also clears the member's failures from the same address or
    device. The caller commits.

    Args:
        attempt_id: The id from ``begin_attempt``.
        username: The member's username.
    """
    row = db.session.get(LoginFailure, attempt_id)
    if row is not None:
        addr = row.ip
        db.session.delete(row)
        clear_failures(username, addr)


def record_failure(username: str | None, ip: str | None) -> None:
    """Store a failed log-in, start any lock it causes, and prune.

    The one-step form of ``begin_attempt`` + ``attempt_failed``
    (without the parallel-request protection). Commits.

    Args:
        username: The username that was tried.
        ip: The client address.
    """
    name, addr, now = _normalise(username), _client_ip(ip), utc_now()
    db.session.add(LoginFailure(username=name, ip=addr, created_at=now))
    db.session.flush()
    _apply_locks(name, addr, now)
    prune()
    db.session.commit()


def clear_failures(username: str | None, ip: str | None = None) -> None:
    """Stop a username's past failures counting.

    After a good log-in only the failures from that address are
    cleared, so someone else's wrong guesses on another device still
    count towards the many-devices limit. The rows stay (marked
    cleared) so admins can still see them. The caller commits.

    Args:
        username: The username to clear.
        ip: Only clear failures from this address; ``None`` clears
            them from every address.
    """
    conds = [LoginFailure.username == _normalise(username),
             LoginFailure.cleared.is_(False)]
    if ip is not None:
        conds.append(LoginFailure.ip == _client_ip(ip))
    db.session.execute(
        db.update(LoginFailure).where(*conds).values(cleared=True))


def admin_clear(username: str | None) -> None:
    """Clear a username's failures and lift its pauses (admins).

    Ends every ``"pair"`` and ``"name"`` lock on that username (from
    any address) and marks its failures cleared so they stop
    counting. Whole-device (``"ip"``) pauses are left alone: they
    may have been caused by guessing other names, so lifting one is
    a separate choice (``unpause_device``). The rows stay so admins
    can still see them. The caller commits.

    Args:
        username: The username to clear, as typed or stored.
    """
    name, now = _normalise(username), utc_now()
    clear_failures(name)
    # Shorten running locks to "now" rather than deleting them, so
    # the failures before them still never count twice (and a
    # revoked device key stays revoked).
    db.session.execute(
        db.update(LoginLock)
        .where(LoginLock.scope.in_(("pair", "name")),
               LoginLock.username == name, LoginLock.locked_until > now)
        .values(locked_until=now))


def unpause_device(ip: str | None) -> bool:
    """Lift a whole-device pause on one address (admins).

    Only the ``"ip"`` lock ends; failures before it never count
    again, and name pauses stay. The caller commits.

    Args:
        ip: The paused address.

    Returns:
        ``True`` if a running pause was lifted.
    """
    addr, now = _client_ip(ip), utc_now()
    result = db.session.execute(
        db.update(LoginLock)
        .where(LoginLock.scope == "ip", LoginLock.ip == addr,
               LoginLock.locked_until > now)
        .values(locked_until=now))
    return result.rowcount > 0


def paused_devices() -> list[dict]:
    """List addresses that are paused as a whole right now.

    Returns:
        One ``{"ip": str, "until": datetime}`` per address, latest
        end first.
    """
    rows = db.session.execute(
        db.select(LoginLock.ip, func.max(LoginLock.locked_until))
        .where(LoginLock.scope == "ip",
               LoginLock.locked_until > utc_now())
        .group_by(LoginLock.ip)
        .order_by(func.max(LoginLock.locked_until).desc())).all()
    return [{"ip": ip, "until": until} for ip, until in rows]


def shared_address() -> str | None:
    """Spot that everyone seems to log in from the same address.

    Looks at the last ``SHARED_SAMPLE`` failures of the past
    ``KEEP_FOR`` (trusted-device keys ignored). That usually means a
    reverse proxy is not listed in ``FB_TRUSTED_PROXIES``, so one
    person's wrong tries pause everyone.

    Returns:
        The address, if at least ``SHARED_MIN_FAILURES`` failures and
        a ``SHARED_SHARE`` of the sample came from it, for at least
        two different usernames; otherwise ``None``.
    """
    rows = db.session.execute(
        db.select(LoginFailure.ip, LoginFailure.username)
        .where(LoginFailure.created_at >= utc_now() - KEEP_FOR,
               LoginFailure.ip.not_like(DEVICE_PREFIX + "%"))
        .order_by(LoginFailure.id.desc())
        .limit(SHARED_SAMPLE)).all()
    if len(rows) < SHARED_MIN_FAILURES:
        return None
    addr, count = Counter(ip for ip, _name in rows).most_common(1)[0]
    names = {name for ip, name in rows if ip == addr}
    if count >= SHARED_SHARE * len(rows) and len(names) >= 2:
        return addr
    return None


def prune() -> None:
    """Delete failures and locks older than ``KEEP_FOR``.

    Pauses of trusted-device keys are kept for ``DEVICE_KEEP_FOR``.

    The caller commits.
    """
    now = utc_now()
    cutoff = now - KEEP_FOR
    db.session.execute(
        db.delete(LoginFailure).where(LoginFailure.created_at < cutoff))
    # Device-key locks are kept as long as the cookie could be valid,
    # so a revoked token never becomes trusted again.
    db.session.execute(
        db.delete(LoginLock).where(
            LoginLock.locked_until < cutoff,
            db.or_(LoginLock.ip.not_like(DEVICE_PREFIX + "%"),
                   LoginLock.locked_until < now - DEVICE_KEEP_FOR)))


def recent_failure_counts() -> dict[str, int]:
    """Count failed log-ins per username over the last ``KEEP_FOR``.

    Returns:
        Lower-cased username mapped to its number of failures,
        including ones later cleared.
    """
    since = utc_now() - KEEP_FOR
    rows = db.session.execute(
        db.select(LoginFailure.username, func.count(LoginFailure.id))
        .where(LoginFailure.created_at >= since)
        .group_by(LoginFailure.username)
    ).all()
    return {name: count for name, count in rows}


def clearable_names() -> dict[str, bool]:
    """List usernames an admin could clear right now.

    Returns:
        Lower-cased username mapped to ``True`` if it is paused right
        now (on some device or everywhere) and ``False`` if it only
        has uncleared failures that are still counting.
    """
    now = utc_now()
    names = {
        name: False for name in db.session.scalars(
            db.select(LoginFailure.username).distinct()
            .where(LoginFailure.cleared.is_(False),
                   LoginFailure.created_at >= now - KEEP_FOR))
    }
    for name in db.session.scalars(
            db.select(LoginLock.username).distinct()
            .where(LoginLock.scope != "ip",
                   LoginLock.locked_until > now)):
        names[name] = True
    return names
