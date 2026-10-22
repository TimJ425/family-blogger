"""Start Family Blogger.

Use ``python run.py`` for the production server (waitress) on
0.0.0.0:8765, or ``python run.py --dev`` for the Flask debug server
with auto-reload (bound to 127.0.0.1 unless ``--host`` is given).
"""
import argparse
import sys

from family_blogger import create_app
from family_blogger.migrations import MigrationError

try:
    app = create_app()
except MigrationError as exc:
    # A calm, readable reason instead of a traceback; nothing changed.
    sys.exit(f"Family Blogger did not start: {exc}")


def main() -> None:
    """Parse command-line options and start the server.

    Runs the Flask debug server with ``--dev``, otherwise waitress.
    """
    parser = argparse.ArgumentParser(
        description="Run the Family Blogger server.")
    parser.add_argument(
        "--dev", action="store_true", help="run the Flask debug server")
    parser.add_argument(
        "--host", default=None,
        help="address to listen on (default 0.0.0.0, or 127.0.0.1 "
             "with --dev)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    host = args.host or ("127.0.0.1" if args.dev else "0.0.0.0")

    if args.dev:
        app.run(host=host, port=args.port, debug=True)
    else:
        from waitress import serve

        print(f"Family Blogger running on http://{host}:{args.port}")
        # Allow a little more than the upload limit for form overhead
        body_limit = app.config["MAX_CONTENT_LENGTH"] + 5 * 1024 * 1024
        serve(
            app, host=host, port=args.port, threads=8,
            max_request_body_size=body_limit, channel_timeout=60)


if __name__ == "__main__":
    main()
