"""One-shot OAuth helper: print a Trakt refresh token for GitHub Actions / local env."""

from __future__ import annotations

import os
import sys
import urllib.parse

import requests

TRAKT_API = "https://api.trakt.tv"
OOB = "urn:ietf:wg:oauth:2.0:oob"


def main() -> None:
    client_id = (os.environ.get("TRAKT_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("TRAKT_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        print(
            "Set TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET (from trakt.tv/oauth/applications).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    q = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": OOB,
        }
    )
    url = f"https://trakt.tv/oauth/authorize?{q}"
    print("Open this URL in a browser, approve the app, then copy the PIN shown:\n")
    print(url, "\n")
    code = input("Paste PIN / code: ").strip()
    if not code:
        raise SystemExit("No code entered.")

    r = requests.post(
        f"{TRAKT_API}/oauth/token",
        json={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": OOB,
            "grant_type": "authorization_code",
        },
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if not r.ok:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    data = r.json()
    refresh = (data.get("refresh_token") or "").strip()
    if not refresh:
        print("No refresh_token in response:", data, file=sys.stderr)
        raise SystemExit(1)
    print("\nAdd this to GitHub Actions secrets (or your shell) as TRAKT_REFRESH_TOKEN:\n")
    print(refresh)
    print(
        "\nIf Trakt later rotates the refresh token, run this script again and update the secret.",
        flush=True,
    )


if __name__ == "__main__":
    main()
