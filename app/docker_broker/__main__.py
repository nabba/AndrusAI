"""``python -m app.docker_broker`` entrypoint."""
from __future__ import annotations

from app.docker_broker.server import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
