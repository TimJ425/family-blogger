"""Tiny, dependency-free schema migrations for the SQLite database.

``db.create_all()`` only creates missing tables; it never alters an
existing one. This module fills that gap so a family can upgrade the
app without losing data.

The schema version lives in SQLite's ``PRAGMA user_version``. Each
migration is a ``Migration(version, description, function)`` in the
ordered ``MIGRATIONS`` list below. On startup ``migrate_database``:

* fresh database: ``create_all()``, then stamps the latest version;
* older database: runs each pending step's read-only ``check`` (so a
  refused upgrade stops before anything is written), copies the
  database with SQLite's online backup API to
  ``<db>.pre-migration-v<old>-<timestamp>.db`` (keeping an identical
  earlier backup instead of adding another), runs each pending step
  in its own transaction (bumping ``user_version`` in the same
  transaction), then ``create_all()`` for brand-new tables;
* current database: just ``create_all()``;
* newer database (a downgrade): refuses to start.

How to add a migration
----------------------

Append a step with the next version number. Steps get a SQLAlchemy
``Connection`` that is already inside a transaction, so just run SQL.
Make steps idempotent so a crash can be re-run safely (real steps
need full Google-style docstrings, as everywhere in the project)::

    def add_nickname(conn: "Connection") -> None:
        '''Add the optional nickname column to user.'''
        if not column_exists(conn, "user", "nickname"):
            conn.exec_driver_sql(
                "ALTER TABLE user ADD COLUMN nickname VARCHAR(80)")

    MIGRATIONS = [
        Migration(1, "Add user.nickname", add_nickname),
    ]

For changes ``ALTER TABLE`` cannot do (new constraints, AUTOINCREMENT,
dropping columns), use ``rebuild_table``, which follows SQLite's
recommended create-copy-drop-rename procedure and keeps every row and
id. The runner turns ``PRAGMA foreign_keys`` off around each step (it
cannot be changed inside a transaction) and checks
``PRAGMA foreign_key_check`` before committing::

    def rebuild_user(conn: "Connection") -> None:
        '''Rebuild user with AUTOINCREMENT ids.'''
        rebuild_table(conn, "user", '''
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username VARCHAR(80) NOT NULL UNIQUE,
                session_token VARCHAR(64)
            )''')

Columns that exist in both the old and the new table are copied;
new columns take their defaults.

If a step can refuse to run (for example because existing data would
break a new constraint), also pass a read-only ``check`` that raises
``MigrationError``; checks run before the backup is taken.
"""
import filecmp
import glob
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask
from sqlalchemy.engine import Connection, Engine

from .models import db


class Migration:
    """One ordered schema change.

    Attributes:
        version: Schema version reached after this step runs.
        description: Short human-readable summary, used in the log.
        func: Callable taking a SQLAlchemy ``Connection``.
        check: Optional read-only callable taking a ``Connection``
            that raises ``MigrationError`` if the step would refuse.
            Checks run before the pre-migration backup is taken, so a
            refused upgrade never leaves a backup behind.
    """

    def __init__(self, version: int, description: str,
                 func: object, check: object = None) -> None:
        """Store the step's details.

        Args:
            version: Schema version reached after this step runs.
            description: Short summary of the change.
            func: Callable taking a ``Connection`` and returning None.
            check: Optional pre-flight callable taking a
                ``Connection``; it must not change anything.
        """
        self.version = version
        self.description = description
        self.func = func
        self.check = check


# Matches the ``user`` table that ``create_all()`` makes from the model.
USER_TABLE_V1 = """
    CREATE TABLE {table} (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        username VARCHAR(30) NOT NULL,
        password_hash VARCHAR(256) NOT NULL,
        display_name VARCHAR(60),
        bio VARCHAR(500),
        avatar VARCHAR(64) NOT NULL,
        is_admin BOOLEAN NOT NULL,
        onboarded BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL,
        session_token VARCHAR(64)
            DEFAULT (lower(hex(randomblob(32)))) NOT NULL,
        theme_preset VARCHAR(20) NOT NULL,
        colour_primary VARCHAR(7),
        colour_accent VARCHAR(7),
        colour_bg VARCHAR(7),
        colour_card VARCHAR(7),
        colour_text VARCHAR(7),
        background_image VARCHAR(64)
    )"""
USER_INDEXES_V1 = [
    "CREATE UNIQUE INDEX ix_user_username_lower ON user (lower(username))",
]


def _uses_autoincrement(conn: Connection, table: str) -> bool:
    """Say whether a table was created with AUTOINCREMENT.

    Args:
        conn: Open connection.
        table: Table name.

    Returns:
        ``True`` if the table's SQL contains ``AUTOINCREMENT``.
    """
    sql = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).scalar()
    return "AUTOINCREMENT" in (sql or "").upper()


def _refuse_duplicate_usernames(conn: Connection) -> None:
    """Stop before the ``lower(username)`` index if it would fail.

    Older versions allowed ``Kid`` and ``kid`` as two accounts. The
    data is left untouched; an admin has to rename one of them (with
    the previous version of the app) before upgrading.

    Args:
        conn: Connection inside the migration transaction.

    Raises:
        MigrationError: Naming each group of clashing usernames.
    """
    rows = conn.exec_driver_sql(
        "SELECT group_concat(username, ', ') FROM user "
        "GROUP BY lower(username) HAVING count(*) > 1 "
        "ORDER BY lower(username)").fetchall()
    if rows:
        clashes = "; ".join(row[0] for row in rows)
        raise MigrationError(
            "Some usernames differ only in capital letters, which this "
            f"version does not allow: {clashes}. Nothing was changed. "
            "Rename one of each pair using the previous version of the "
            "app (or restore a backup), then start this version again.")


def _user_sessions_needed(conn: Connection) -> bool:
    """Say whether ``user_sessions_and_ids`` still has work to do.

    Args:
        conn: Open connection.

    Returns:
        ``True`` unless ``user`` already has ``session_token`` and
        ``AUTOINCREMENT`` ids.
    """
    return not (column_exists(conn, "user", "session_token")
                and _uses_autoincrement(conn, "user"))


def check_user_sessions(conn: Connection) -> None:
    """Pre-flight check for ``user_sessions_and_ids``.

    Args:
        conn: Open connection (read-only use).

    Raises:
        MigrationError: If two usernames differ only in case.
    """
    if _user_sessions_needed(conn):
        _refuse_duplicate_usernames(conn)


def user_sessions_and_ids(conn: Connection) -> None:
    """Add ``user.session_token`` and stop user ids being reused.

    Rebuilds ``user`` with ``AUTOINCREMENT`` so a deleted member's id
    (and any old cookie naming it) never points at someone new. Every
    row keeps its id, so posts, comments, reactions and photos stay
    linked. The column default gives each existing row its own random
    64-character token. Does nothing if both changes are already in
    place.

    Args:
        conn: Connection inside the migration transaction.

    Raises:
        MigrationError: If two usernames differ only in case, since
            the new unique index on ``lower(username)`` would fail.
    """
    if not _user_sessions_needed(conn):
        return
    _refuse_duplicate_usernames(conn)
    rebuild_table(conn, "user", USER_TABLE_V1, USER_INDEXES_V1)
    # Belt and braces: give any blank or shared token a fresh value.
    conn.exec_driver_sql(
        "UPDATE user SET session_token = lower(hex(randomblob(32))) "
        "WHERE session_token IS NULL OR session_token = '' OR "
        "session_token IN (SELECT session_token FROM user "
        "GROUP BY session_token HAVING count(*) > 1)")


# Matches the ``login_failure`` table ``create_all()`` makes from the
# ``LoginFailure`` model.
LOGIN_FAILURE_TABLE_V2 = """
    CREATE TABLE IF NOT EXISTS login_failure (
        id INTEGER NOT NULL,
        username VARCHAR(80) NOT NULL,
        ip VARCHAR(45) NOT NULL,
        created_at DATETIME NOT NULL,
        cleared BOOLEAN NOT NULL,
        PRIMARY KEY (id)
    )"""
LOGIN_FAILURE_INDEXES_V2 = [
    "CREATE INDEX IF NOT EXISTS ix_login_failure_created_at "
    "ON login_failure (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_login_failure_username "
    "ON login_failure (username)",
    "CREATE INDEX IF NOT EXISTS ix_login_failure_ip "
    "ON login_failure (ip)",
]


def add_login_failures(conn: Connection) -> None:
    """Add the ``login_failure`` table used for log-in throttling.

    Only creates a new, empty table and its indexes; no existing data
    is touched. Safe to re-run.

    Args:
        conn: Connection inside the migration transaction.
    """
    conn.exec_driver_sql(LOGIN_FAILURE_TABLE_V2)
    for sql in LOGIN_FAILURE_INDEXES_V2:
        conn.exec_driver_sql(sql)


# Matches the ``login_lock`` table ``create_all()`` makes from the
# ``LoginLock`` model.
LOGIN_LOCK_TABLE_V3 = """
    CREATE TABLE IF NOT EXISTS login_lock (
        id INTEGER NOT NULL,
        scope VARCHAR(8) NOT NULL,
        username VARCHAR(80) NOT NULL,
        ip VARCHAR(45) NOT NULL,
        started_at DATETIME NOT NULL,
        locked_until DATETIME NOT NULL,
        PRIMARY KEY (id)
    )"""
LOGIN_LOCK_INDEXES_V3 = [
    "CREATE INDEX IF NOT EXISTS ix_login_lock_username "
    "ON login_lock (username)",
    "CREATE INDEX IF NOT EXISTS ix_login_lock_ip ON login_lock (ip)",
    "CREATE INDEX IF NOT EXISTS ix_login_lock_locked_until "
    "ON login_lock (locked_until)",
]


def add_login_locks(conn: Connection) -> None:
    """Add the ``login_lock`` table used for log-in throttling.

    Only creates a new, empty table and its indexes; no existing data
    is touched. Safe to re-run.

    Args:
        conn: Connection inside the migration transaction.
    """
    conn.exec_driver_sql(LOGIN_LOCK_TABLE_V3)
    for sql in LOGIN_LOCK_INDEXES_V3:
        conn.exec_driver_sql(sql)


# Ordered, ascending by version, with no gaps. Append new steps here.
MIGRATIONS = [
    Migration(1, "Add user.session_token; never reuse user ids",
              user_sessions_and_ids, check_user_sessions),
    Migration(2, "Add login_failure for log-in throttling",
              add_login_failures),
    Migration(3, "Add login_lock for fairer log-in throttling",
              add_login_locks),
]


class MigrationError(RuntimeError):
    """Raised when the database cannot be safely migrated."""


def latest_version(migrations: list["Migration"] | None = None) -> int:
    """Return the schema version the code expects.

    Args:
        migrations: Steps to use instead of ``MIGRATIONS``.

    Returns:
        The highest step version, or 0 if there are no steps.
    """
    steps = MIGRATIONS if migrations is None else migrations
    return max((m.version for m in steps), default=0)


def column_exists(conn: Connection, table: str, column: str) -> bool:
    """Say whether a table has a column.

    Args:
        conn: Open connection.
        table: Table name.
        column: Column name.

    Returns:
        ``True`` if the column exists.
    """
    rows = conn.exec_driver_sql(
        f'PRAGMA table_info("{table}")').fetchall()
    return any(row[1] == column for row in rows)


def table_exists(conn: Connection, table: str) -> bool:
    """Say whether a table exists.

    Args:
        conn: Open connection.
        table: Table name.

    Returns:
        ``True`` if the table exists.
    """
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    return row is not None


def rebuild_table(conn: Connection, table: str, create_sql: str,
                  indexes: list[str] | None = None,
                  drop_columns: list[str] | None = None) -> None:
    """Rebuild a table with a new definition, keeping all rows.

    Must run inside a migration step (a transaction with foreign keys
    switched off by the runner). Creates a new table, copies the
    columns present in both, drops the old table, renames the new one
    and recreates indexes. Primary key values are copied as they are.
    Old columns missing from the new definition stop the rebuild,
    unless they are named in ``drop_columns``, so data is never
    discarded by accident.

    Args:
        conn: Connection inside the migration transaction.
        table: Name of the table to rebuild.
        create_sql: ``CREATE TABLE {table} (...)`` statement for the
            new definition; ``{table}`` is replaced by a temporary
            name that is renamed to ``table`` afterwards.
        indexes: ``CREATE INDEX`` statements for the new table. If
            ``None``, the existing indexes are recreated unchanged.
        drop_columns: Old columns the step means to remove.

    Raises:
        MigrationError: If the table does not exist, or an old column
            would be lost without being named in ``drop_columns``.
    """
    if not table_exists(conn, table):
        raise MigrationError(f"Cannot rebuild missing table {table}")
    temp = f"{table}__new"
    if indexes is None:
        indexes = [
            row[0] for row in conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name=? AND sql IS NOT NULL", (table,))]
    conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{temp}"')
    conn.exec_driver_sql(create_sql.format(table=f'"{temp}"'))
    old_cols = [r[1] for r in conn.exec_driver_sql(
        f'PRAGMA table_info("{table}")')]
    new_cols = [r[1] for r in conn.exec_driver_sql(
        f'PRAGMA table_info("{temp}")')]
    lost = [c for c in old_cols
            if c not in new_cols and c not in (drop_columns or [])]
    if lost:
        raise MigrationError(
            f"Rebuilding {table} would lose columns: {', '.join(lost)}")
    shared = ", ".join(f'"{c}"' for c in new_cols if c in old_cols)
    conn.exec_driver_sql(
        f'INSERT INTO "{temp}" ({shared}) '
        f'SELECT {shared} FROM "{table}"')
    conn.exec_driver_sql(f'DROP TABLE "{table}"')
    conn.exec_driver_sql(f'ALTER TABLE "{temp}" RENAME TO "{table}"')
    for sql in indexes:
        conn.exec_driver_sql(sql)


def _get_version(conn: Connection) -> int:
    """Read ``PRAGMA user_version``.

    Args:
        conn: Open connection.

    Returns:
        The stored schema version.
    """
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar())


def _set_version(conn: Connection, version: int) -> None:
    """Write ``PRAGMA user_version``.

    Args:
        conn: Open connection.
        version: Version to store.
    """
    conn.exec_driver_sql(f"PRAGMA user_version = {int(version)}")


def _has_tables(conn: Connection) -> bool:
    """Say whether the database contains any user tables.

    Args:
        conn: Open connection.

    Returns:
        ``True`` if at least one table exists.
    """
    row = conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchone()
    return row is not None


def _copy_database(db_path: str, dest: str) -> None:
    """Copy a database with SQLite's online backup API.

    Args:
        db_path: Path of the live database file.
        dest: Path of the copy to write.
    """
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _backup_database(db_path: str, old_version: int) -> tuple[str, bool]:
    """Back up the database before migrating, without piling up copies.

    A fresh copy is always made first. If the newest existing backup
    for the same ``old_version`` holds exactly the same bytes (the
    database has not changed since, e.g. a step failed and the app
    was restarted), the fresh copy is deleted and the existing one is
    kept. Otherwise the fresh copy is kept under a new name, so a
    backup is never overwritten and new data is never left without
    one.

    Args:
        db_path: Path of the live database file.
        old_version: Schema version before migrating, for the name.

    Returns:
        The path of the backup that covers the current data, and
        ``True`` if it was newly made (``False`` if an identical
        earlier backup was kept instead).
    """
    live = Path(db_path)
    pattern = glob.escape(live.name) + f".pre-migration-v{old_version}-*.db"
    existing = sorted(live.parent.glob(pattern),
                      key=lambda p: p.stat().st_mtime)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(f"{db_path}.pre-migration-v{old_version}-{stamp}.db")
    extra = 1
    while dest.exists():
        dest = Path(f"{db_path}.pre-migration-v{old_version}-{stamp}"
                    f"-{extra}.db")
        extra += 1
    fresh = dest.with_name(dest.name + ".partial")
    try:
        _copy_database(db_path, str(fresh))
    except BaseException:
        fresh.unlink(missing_ok=True)
        raise
    if existing and filecmp.cmp(fresh, existing[-1], shallow=False):
        fresh.unlink()
        return str(existing[-1]), False
    fresh.rename(dest)
    return str(dest), True


def _run_step(engine: Engine, step: "Migration") -> None:
    """Run one step atomically and bump the version with it.

    Foreign keys are off while the step runs (needed for table
    rebuilds) and a foreign key check must pass before committing.

    Args:
        engine: The database engine.
        step: The migration to run.

    Raises:
        MigrationError: If the step leaves broken foreign keys.
    """
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
        try:
            conn.exec_driver_sql("BEGIN")
            try:
                step.func(conn)
                bad = conn.exec_driver_sql(
                    "PRAGMA foreign_key_check").fetchall()
                if bad:
                    raise MigrationError(
                        f"Foreign key check failed after {step.version}")
                _set_version(conn, step.version)
                conn.exec_driver_sql("COMMIT")
            except BaseException:
                conn.exec_driver_sql("ROLLBACK")
                raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys = ON")


def migrate_database(app: Flask,
                     migrations: list["Migration"] | None = None) -> None:
    """Create or upgrade the database schema. Call in an app context.

    Args:
        app: The application (its logger and database are used).
        migrations: Steps to use instead of ``MIGRATIONS`` (tests).

    Raises:
        MigrationError: If the database is newer than this code or the
            steps are not numbered 1..N in order.
    """
    steps = sorted(MIGRATIONS if migrations is None else migrations,
                   key=lambda m: m.version)
    latest = latest_version(steps)
    if [m.version for m in steps] != list(range(1, latest + 1)):
        raise MigrationError("Migration versions must run 1..N")
    engine = db.engine
    with engine.connect() as conn:
        current = _get_version(conn)
        existing = _has_tables(conn)

    if not existing:
        db.create_all()
        with engine.connect() as conn:
            _set_version(conn, latest)
            conn.commit()
        app.logger.info("New database created at schema version %d",
                        latest)
        return
    if current > latest:
        raise MigrationError(
            f"Database schema version {current} is newer than this "
            f"app supports ({latest}). Refusing to start; install a "
            "newer version or restore a backup.")
    pending = [m for m in steps if m.version > current]
    if pending:
        # Refuse before taking a backup, so restarting a refused
        # upgrade again and again cannot fill the disk with copies.
        with engine.connect() as conn:
            for step in pending:
                if step.check is not None:
                    step.check(conn)
        db_path = engine.url.database
        if db_path and db_path != ":memory:" and Path(db_path).exists():
            backup, made = _backup_database(db_path, current)
            if made:
                app.logger.info("Pre-migration backup saved to %s",
                                backup)
            else:
                app.logger.info(
                    "Database unchanged since the pre-migration backup "
                    "%s; keeping that one instead of another copy",
                    backup)
        for step in pending:
            app.logger.info("Migrating to v%d: %s", step.version,
                            step.description)
            _run_step(engine, step)
        app.logger.info("Database migrated from v%d to v%d", current,
                        latest)
    db.create_all()
