"""Check the project's Python coding rules that flake8 cannot enforce.

Fails on variable annotations, ``typing``/``collections.abc`` imports,
missing docstrings and unannotated arguments or return values. See
CONTRIBUTING.md for the rules themselves.

Usage: ``.venv/bin/python tests/test_conventions.py``
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "instance", "__pycache__", "data", "backups"}
BANNED_MODULES = {"typing", "typing_extensions", "collections.abc"}


def python_files() -> list[Path]:
    """List the project's Python files, skipping data and environments.

    Returns:
        Sorted paths of every ``.py`` file under the project root.
    """
    return sorted(
        path for path in ROOT.rglob("*.py")
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts)
    )


def is_banned(module: str | None) -> bool:
    """Decide whether an imported module name breaks the typing rule.

    Args:
        module: The dotted module name from an import statement.

    Returns:
        ``True`` if the module is ``typing`` or ``collections.abc``
        (or a submodule of either).
    """
    if not module:
        return False
    return any(
        module == banned or module.startswith(banned + ".")
        for banned in BANNED_MODULES
    )


def check_function(node: ast.FunctionDef | ast.AsyncFunctionDef,
                   where: str) -> list[str]:
    """Check one function for a docstring and full annotations.

    Args:
        node: The function definition to inspect.
        where: ``path:line`` prefix used in problem messages.

    Returns:
        A message for each rule the function breaks.
    """
    problems = []
    if not ast.get_docstring(node):
        problems.append(f"{where} function '{node.name}' has no docstring")
    args = node.args
    every_arg = args.posonlyargs + args.args + args.kwonlyargs
    every_arg += [arg for arg in (args.vararg, args.kwarg) if arg]
    for arg in every_arg:
        if arg.annotation is None and arg.arg not in ("self", "cls"):
            problems.append(
                f"{where} argument '{arg.arg}' of '{node.name}' "
                "has no type hint"
            )
    if node.returns is None:
        problems.append(f"{where} function '{node.name}' has no return hint")
    return problems


def check_file(path: Path) -> list[str]:
    """Check a single Python file against the coding rules.

    Args:
        path: The file to check.

    Returns:
        A message for each problem found, empty when the file is clean.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    name = path.relative_to(ROOT)
    problems = []
    if not ast.get_docstring(tree):
        problems.append(f"{name}:1 module has no docstring")
    for node in ast.walk(tree):
        where = f"{name}:{getattr(node, 'lineno', 1)}"
        if isinstance(node, ast.AnnAssign):
            problems.append(f"{where} variable type hints are not allowed")
        elif isinstance(node, ast.Import):
            problems += [
                f"{where} import of '{alias.name}' is not allowed"
                for alias in node.names if is_banned(alias.name)
            ]
        elif isinstance(node, ast.ImportFrom) and is_banned(node.module):
            problems.append(f"{where} import from '{node.module}' "
                            "is not allowed")
        elif isinstance(node, ast.ClassDef) and not ast.get_docstring(node):
            problems.append(f"{where} class '{node.name}' has no docstring")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            problems += check_function(node, where)
    return problems


def main() -> int:
    """Check every project file and report any rule violations.

    Returns:
        ``0`` when every file follows the rules, otherwise ``1``.
    """
    files = python_files()
    problems = [problem for path in files for problem in check_file(path)]
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"FAILS: {len(problems)} ({len(files)} files checked)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
