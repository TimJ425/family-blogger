"""Tests for the SQLite migration runner (temporary file databases)."""
import atexit
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from flask import Flask  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402

from family_blogger import create_app, migrations  # noqa: E402
from family_blogger.migrations import (  # noqa: E402
    Migration,
    MigrationError,
    column_exists,
    migrate_database,
    rebuild_table,
)

SCRATCH = tempfile.mkdtemp(prefix="fb-mig-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
passed = 0
counter = 0


def check(condition: bool, label: str) -> None:
    """Record one check, exiting on the first failure.

    Args:
        condition: Whether the check passed.
        label: Description shown on failure.
    """
    global passed
    if not condition:
        print(f"FAIL: {label}")
        sys.exit(1)
    passed += 1


def new_dir() -> Path:
    """Make a fresh empty directory inside the scratch folder.

    Returns:
        The directory path.
    """
    global counter
    counter += 1
    path = Path(SCRATCH) / f"case{counter}"
    path.mkdir()
    return path


def make_app(directory: Path) -> Flask:
    """Build the real app, with its database in ``directory``.

    Args:
        directory: Instance directory to use.

    Returns:
        The configured app.
    """
    return create_app({"INSTANCE_DIR": str(directory), "TESTING": True})


def db_file(directory: Path) -> Path:
    """Return the database path inside an instance directory.

    Args:
        directory: Instance directory.

    Returns:
        Path of ``family_blogger.db``.
    """
    return directory / "family_blogger.db"


def version_of(path: Path) -> int:
    """Read ``user_version`` straight from a database file.

    Args:
        path: Database file.

    Returns:
        The stored version.
    """
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def rows(path: Path, sql: str) -> list:
    """Run a query on a database file.

    Args:
        path: Database file.
        sql: Query text.

    Returns:
        All result rows.
    """
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def make_legacy(directory: Path) -> Path:
    """Create a small version-0 database with a few rows.

    Args:
        directory: Instance directory.

    Returns:
        The database path.
    """
    path = db_file(directory)
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT,"
        " person_id INTEGER REFERENCES person(id));"
        "CREATE INDEX ix_note_person ON note (person_id);"
        "INSERT INTO person VALUES (3, 'Ann'), (7, 'Bo');"
        "INSERT INTO note VALUES (10, 'hi', 7), (11, 'yo', 3);")
    con.commit()
    con.close()
    return path


def add_age(conn: Connection) -> None:
    """Test step: add person.age idempotently.

    Args:
        conn: Connection inside the migration transaction.
    """
    ORDER.append(1)
    if not column_exists(conn, "person", "age"):
        conn.exec_driver_sql("ALTER TABLE person ADD COLUMN age INT")


def rebuild_person(conn: Connection) -> None:
    """Test step: rebuild person with AUTOINCREMENT.

    Args:
        conn: Connection inside the migration transaction.
    """
    ORDER.append(2)
    rebuild_table(conn, "person", (
        "CREATE TABLE {table} (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " name TEXT NOT NULL, age INT, extra TEXT DEFAULT 'x')"))


def explode(conn: Connection) -> None:
    """Test step: change data, then fail.

    Args:
        conn: Connection inside the migration transaction.

    Raises:
        RuntimeError: Always.
    """
    conn.exec_driver_sql("DELETE FROM person")
    raise RuntimeError("boom")


ORDER = []


def run_in_app(directory: Path, steps: list) -> None:
    """Run ``migrate_database`` with injected steps on a legacy DB.

    Args:
        directory: Instance directory holding the database.
        steps: Migration steps to use.
    """
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_file(directory)}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False)
    migrations.db.init_app(app)
    with app.app_context():
        migrate_database(app, steps)
        migrations.db.engine.dispose()


# --- fresh database ---
d = new_dir()
make_app(d)
check(version_of(db_file(d)) == migrations.latest_version(),
      "fresh db stamped with latest version")
check(not list(d.glob("*.pre-migration-*")), "no backup for fresh db")
make_app(d)
check(not list(d.glob("*.pre-migration-*")), "no backup when current")

# --- in-memory database still works ---
mem = create_app({"INSTANCE_DIR": str(new_dir()), "TESTING": True,
                  "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
check(mem is not None, "in-memory app starts")

# --- pending steps run in order, with a backup ---
d = new_dir()
path = make_legacy(d)
steps = [Migration(1, "add age", add_age),
         Migration(2, "rebuild person", rebuild_person)]
run_in_app(d, steps)
check(ORDER == [1, 2], f"steps ran in order {ORDER}")
check(version_of(path) == 2, "version bumped to 2")
check(rows(path, "SELECT id, name FROM person ORDER BY id")
      == [(3, "Ann"), (7, "Bo")], "rebuild kept rows and ids")
check(rows(path, "SELECT id, person_id FROM note ORDER BY id")
      == [(10, 7), (11, 3)], "linked rows intact")
check(rows(path, "SELECT extra FROM person WHERE id=3") == [("x",)],
      "new column took default")
check(rows(path, "PRAGMA foreign_key_check") == [], "fks consistent")
check("AUTOINCREMENT" in rows(
    path, "SELECT sql FROM sqlite_master WHERE name='person'")[0][0],
    "table is AUTOINCREMENT")
check(rows(path, "SELECT name FROM sqlite_master WHERE "
           "name='ix_note_person'") != [], "index on other table kept")
backups = list(d.glob("family_blogger.db.pre-migration-v0-*.db"))
check(len(backups) == 1, "one pre-migration backup")
check(rows(backups[0], "SELECT id, name FROM person ORDER BY id")
      == [(3, "Ann"), (7, "Bo")], "backup holds original rows")
check(version_of(backups[0]) == 0, "backup keeps old version")

# --- idempotent re-run ---
ORDER.clear()
run_in_app(d, steps)
check(ORDER == [], "nothing re-run at latest")
check(len(list(d.glob("*.pre-migration-*"))) == 1,
      "no extra backup at latest")
con = sqlite3.connect(path)
con.execute("PRAGMA user_version = 0")
con.commit()
con.close()
run_in_app(d, steps)
check(version_of(path) == 2 and rows(
    path, "SELECT count(*) FROM person") == [(2,)],
    "re-run after simulated crash is safe")

# --- failing step rolls back ---
d = new_dir()
path = make_legacy(d)
bad = [Migration(1, "add age", add_age),
       Migration(2, "explode", explode)]
try:
    run_in_app(d, bad)
    failed = False
except RuntimeError:
    failed = True
check(failed, "failing step raises")
check(version_of(path) == 1, "version stays at last good step")
check(rows(path, "SELECT count(*) FROM person") == [(2,)],
      "failed step rolled back data")
check(len(list(d.glob("*.pre-migration-v0-*"))) == 1,
      "backup kept after failure")

# --- rebuild never drops columns by accident ---
d = new_dir()
path = make_legacy(d)
con = sqlite3.connect(path)
con.execute("ALTER TABLE person ADD COLUMN nickname TEXT")
con.execute("UPDATE person SET nickname = 'Annie' WHERE id = 3")
con.commit()
con.close()
try:
    run_in_app(d, steps)
    refused = False
except MigrationError:
    refused = True
check(refused, "rebuild refused when a column would be lost")
check(rows(path, "SELECT nickname FROM person WHERE id = 3")
      == [("Annie",)], "unknown column data kept")
check(version_of(path) == 1,
      "version stops before the refused rebuild step")

# --- downgrade refused ---
d = new_dir()
path = make_legacy(d)
con = sqlite3.connect(path)
con.execute("PRAGMA user_version = 5")
con.commit()
con.close()
try:
    run_in_app(d, steps)
    refused = False
except MigrationError:
    refused = True
check(refused, "newer database refused")
check(version_of(path) == 5 and not list(d.glob("*.pre-migration-*")),
      "refused db untouched")

print(f"ALL {passed} CHECKS PASSED")
