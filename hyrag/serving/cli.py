"""serving/cli.py — start the embedding service.

    uv run python -m hyrag.serving.cli serve                  # 127.0.0.1:8001
    uv run python -m hyrag.serving.cli serve --port 8002

Nothing computes here; the app lives in `hyrag.serving.embeddings`. The port is a flag
because the report should be able to say which port the rows agreed on, and because a stale
process on 8001 must be a config change rather than a code edit.
"""

from __future__ import annotations

import argparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hyrag.serving.app import app

    print(f"  serving on http://{args.host}:{args.port}  (/health · /v1/embeddings · /v1/chat/completions)")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyrag.serving.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the OpenAI-compatible embedding endpoint")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
