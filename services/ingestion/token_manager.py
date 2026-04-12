"""
token_manager.py
─────────────────
OAuth 2.0 token lifecycle for Gmail access.

First run  : triggers a browser-based OAuth flow → saves token.json
Subsequent : loads token.json, auto-refreshes if expired (no user action needed)

Environment variables (set in .env):
  GOOGLE_CREDENTIALS_PATH  Path to your client_secret_....json
  GOOGLE_TOKEN_PATH        Where to store/load the OAuth token (default: token/token.json)
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

logger = logging.getLogger(__name__)

# Read-only access to Gmail is all we need
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_credentials() -> Credentials:
    """Return valid Gmail credentials, running OAuth flow on first use.

    Token is persisted to GOOGLE_TOKEN_PATH so subsequent calls are silent.
    Raises EnvironmentError if GOOGLE_CREDENTIALS_PATH is not set.
    """
    creds_path = _require_env("GOOGLE_CREDENTIALS_PATH")
    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", "token/token.json"))
    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds: Credentials | None = None

    # ── Load existing token ───────────────────────────────────────────────────
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    # ── Refresh or re-authorise ───────────────────────────────────────────────
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Gmail token…")
            creds.refresh(Request())
        else:
            logger.info("No valid token found — starting OAuth flow…")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), SCOPES
            )
            # Opens a browser tab; user grants access once, ever.
            creds = flow.run_local_server(port=0)

        # Persist so next run is silent
        token_path.write_text(creds.to_json())
        logger.info("Token saved to %s", token_path)

    return creds


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"{key} is not set. Add it to your .env file."
        )
    return value


# ── Standalone entrypoint (used by `make auth`) ───────────────────────────────

if __name__ == "__main__":
    import sys
    print("=" * 60)
    print("  EMLI — Gmail One-Time OAuth Setup")
    print("=" * 60)
    print()
    print("A browser window will open. Sign in with your Google")
    print("account and grant access. You only need to do this once.")
    print()
    try:
        creds = get_credentials()
        token_path = os.environ.get("GOOGLE_TOKEN_PATH", "token/token.json")
        print()
        print(f"✓ Token saved to {token_path}")
        print("  Run `make fetch` or `docker compose up` to start fetching emails.")
    except Exception as exc:
        print(f"\n✗ Auth failed: {exc}", file=sys.stderr)
        sys.exit(1)
