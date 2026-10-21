"""Start Family Blogger.

Use ``python run.py`` for the production server (waitress) on
0.0.0.0:8765, or ``python run.py --dev`` for the Flask debug server
with auto-reload.
"""
import argparse

from family_blogger import create_app

app = create_app()


def main() -> None:
    """Parse command-line options and start the server.

    Runs the Flask debug server with ``--dev``, otherwise waitress.
    """
    parser = argparse.ArgumentParser(
        description="Run the Family Blogger server.")
    parser.add_argument(
        "--dev", action="store_true", help="run the Flask debug server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.dev:
        app.run(host=args.host, port=args.port, debug=True)
    else:
        from waitress import serve

        print(f"Family Blogger running on http://{args.host}:{args.port}")
        serve(app, host=args.host, port=args.port, threads=8)


if __name__ == "__main__":
    main()
