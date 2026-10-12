"""Entry point: ``python -m jukebox``."""

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    config = load_config()
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        proxy_headers=False,  # handled by ProxyMiddleware (only from TRUSTED_PROXIES)
        log_config=None,
        ws_ping_interval=20,
        ws_ping_timeout=20,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
