"""Load .env and configure HTTP proxy (for PowerShell / shell wrappers)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv

load_dotenv(APP_DIR / ".env", override=True)

from network_env import configure_http_proxy

if __name__ == "__main__":
    mode = configure_http_proxy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "NO_PROXY_DEFAULT"):
        print(f"{key}={os.environ.get(key, '')}")
    print(f"NETWORK_MODE={mode}", file=sys.stderr)
