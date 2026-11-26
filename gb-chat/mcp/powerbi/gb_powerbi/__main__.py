"""Run the GB Power BI MCP server: python -m gb_powerbi"""

from __future__ import annotations

import logging

import uvicorn

from .server import build_app
from .settings import load_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    logging.getLogger("gb_powerbi").info(
        "GB Power BI MCP on http://%s:%s/mcp (tenancy %s)",
        settings.host, settings.port, settings.tenancy_url)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
