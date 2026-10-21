"""Set up the gaming Facebook Page + Instagram account ("facebook_gaming" / "instagram_gaming").

1. Graph API Explorer (https://developers.facebook.com/tools/explorer): pick the app, add the permissions
   pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, instagram_content_publish,
   business_management, click Generate Access Token and allow the GAMING page and its Instagram account.
2. Access Token Debugger (https://developers.facebook.com/tools/debug/accesstoken): paste that token and click
   "Extend Access Token" - a page token taken from an extended user token does not expire.
3. Put the extended token in backend/.env as META_GAMING_USER_TOKEN=... and run:

    python scripts/meta_gaming_login.py ["Gaming page name"]

Writes META_GAMING_PAGE_ID, META_GAMING_IG_USER_ID and META_GAMING_ACCESS_TOKEN to .env and removes the user token.
Tokens are never printed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
GRAPH = "https://graph.facebook.com/v21.0"


def _set_env(values: dict[str, str | None]) -> None:
    text = ENV.read_text(encoding="utf-8")
    for key, value in values.items():
        text = re.sub(rf"^{key}=.*\n?", "", text, flags=re.M)
        if value is not None:
            text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV.write_text(text, encoding="utf-8")


def main() -> None:
    env = dotenv_values(ENV)
    user_token = env.get("META_GAMING_USER_TOKEN")
    if not user_token:
        sys.exit("Add META_GAMING_USER_TOKEN=<extended user token> to backend/.env first (see the steps at the top).")
    pages = httpx.get(f"{GRAPH}/me/accounts", timeout=30, params={
        "access_token": user_token, "fields": "id,name,access_token,instagram_business_account{id,username}"}).json()
    if "error" in pages:
        sys.exit(f"Meta error: {pages['error'].get('message')}")
    pages = [p for p in pages.get("data", []) if p["id"] != env.get("META_PAGE_ID")]  # skip the movies page
    if len(sys.argv) > 1:
        pages = [p for p in pages if p["name"].casefold() == sys.argv[1].casefold()]
    print("Pages this token can manage:", [(p["name"], (p.get("instagram_business_account") or {}).get("username")) for p in pages])
    if len(pages) != 1:
        sys.exit("Expected exactly one gaming page - pass its name: python scripts/meta_gaming_login.py \"Page name\"")
    page = pages[0]
    ig = page.get("instagram_business_account") or {}
    if not ig:
        print("Warning: no Instagram professional account is linked to this page - only Facebook will be set up.")
    dbg = httpx.get(f"{GRAPH}/debug_token", timeout=30, params={"input_token": page["access_token"],
                                                                "access_token": page["access_token"]}).json().get("data", {})
    _set_env({"META_GAMING_PAGE_ID": page["id"], "META_GAMING_IG_USER_ID": ig.get("id"),
              "META_GAMING_ACCESS_TOKEN": page["access_token"], "META_GAMING_USER_TOKEN": None})
    expires = dbg.get("expires_at")
    print(f"Saved gaming page '{page['name']}'" + (f" + Instagram @{ig['username']}" if ig else "")
          + f". Page token expires: {'never' if not expires else expires}. Scopes: {dbg.get('scopes')}")


if __name__ == "__main__":
    main()
