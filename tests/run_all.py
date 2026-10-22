"""Run every Family Blogger test script and summarise the results.

Usage: ``.venv/bin/python tests/run_all.py``
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITES = [
    "test_backend.py",
    "test_migrations.py",
    "test_frontend.py",
    "test_mobile.py",
    "test_confirmations.py",
    "test_accounts.py",
    "test_security.py",
    "test_bugfixes.py",
    "test_review_fixes.py",
    "test_final_fixes.py",
    "test_conventions.py",
]


def run_suite(suite: str) -> bool:
    """Run one test script and print its outcome.

    Failure lines (or the output tail, if there are none) are printed
    for a failing suite so the cause is visible.

    Args:
        suite: File name of the test script inside ``tests/``.

    Returns:
        ``True`` if the script exited successfully.
    """
    result = subprocess.run(
        [sys.executable, str(HERE / suite)], capture_output=True, text=True
    )
    lines = (result.stdout + result.stderr).strip().splitlines()
    passed = result.returncode == 0
    if not passed:
        failures = [line for line in lines if line.startswith("FAIL")]
        for line in failures or lines[-20:]:
            print(f"  {line}")
    summary = lines[-1] if lines else "no output"
    print(f"{'PASS' if passed else 'FAIL'}  {suite}  ({summary})")
    return passed


def main() -> int:
    """Run all suites and print an overall summary.

    Returns:
        ``0`` when every suite passes, otherwise ``1``.
    """
    failed = [suite for suite in SUITES if not run_suite(suite)]
    print(f"\n{len(SUITES) - len(failed)}/{len(SUITES)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
