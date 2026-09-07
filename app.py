from __future__ import annotations

import argparse
from pathlib import Path

from todo.http import make_server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="todos.sqlite3")
    args = parser.parse_args()

    server = make_server(args.host, args.port, Path(args.db))
    print(f"Todo List running at http://{args.host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
