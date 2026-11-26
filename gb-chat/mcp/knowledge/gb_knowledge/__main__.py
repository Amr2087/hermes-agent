"""Run the GB Knowledge MCP server: python -m gb_knowledge"""

from __future__ import annotations

import logging

import uvicorn

from .server import build_app
from .settings import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    logging.getLogger("gb_knowledge").info(
        "GB Knowledge MCP on http://%s:%s/mcp (tenancy %s, retrieval %s)",
        settings.host, settings.port, settings.tenancy_url, settings.retrieval_url)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
