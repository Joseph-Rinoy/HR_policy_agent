"""Entra ID (Azure AD) sign-in — the IdP-specific token acquisition.

Qubi acquires a user access token for a target system's app-registration scope
and sends it as a bearer token to that system's MCP server. The MCP server
validates it and exchanges it (On-Behalf-Of) for a downstream JWT, so every
existing role/tenant check on the underlying API applies unchanged.

This module is intentionally tiny and synchronous (MSAL's public-client API is
sync). The token cache is persisted next to the app so the interactive browser
login only happens once, then ``acquire_token_silent`` refreshes quietly.

This is the ONLY place that knows about MSAL/Entra. Callers go through
:mod:`identity` (``mint_downstream_token``) so no Entra-ism leaks into the
orchestrator, gateway, or servers. ``get_entra_token`` takes the *scope* to
acquire — the per-system scope mapping lives in :mod:`identity`.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    from paths import app_base_dir

    load_dotenv(app_base_dir() / ".env")
except ImportError:  # python-dotenv is optional; env vars may be set directly
    pass

import msal

from paths import app_base_dir


ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID", "")
ENTRA_CLIENT_ID = os.getenv("ENTRA_CLIENT_ID", "")

_AUTHORITY = (
    f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}" if ENTRA_TENANT_ID else ""
)
_CACHE_PATH = app_base_dir() / ".msal_token_cache.json"


class EntraAuthError(RuntimeError):
    """Raised when an Entra access token cannot be obtained."""


def is_configured() -> bool:
    """True when the base Entra settings (tenant + client) are present.

    The per-system scope is checked by :mod:`identity`, not here.
    """
    return bool(ENTRA_TENANT_ID and ENTRA_CLIENT_ID)


# Lazily-built singletons so importing this module never does I/O or network.
_cache: msal.SerializableTokenCache | None = None
_app: msal.PublicClientApplication | None = None


def _load_cache() -> msal.SerializableTokenCache:
    global _cache
    if _cache is None:
        _cache = msal.SerializableTokenCache()
        try:
            if _CACHE_PATH.exists():
                _cache.deserialize(_CACHE_PATH.read_text(encoding="utf-8"))
        except OSError:
            pass  # a corrupt/unreadable cache just means a fresh login
    return _cache


def _save_cache() -> None:
    if _cache is not None and _cache.has_state_changed:
        try:
            _CACHE_PATH.write_text(_cache.serialize(), encoding="utf-8")
        except OSError:
            pass  # persistence is best-effort; we still have the in-memory token


def _get_app() -> msal.PublicClientApplication:
    global _app
    if _app is None:
        _app = msal.PublicClientApplication(
            client_id=ENTRA_CLIENT_ID,
            authority=_AUTHORITY,
            token_cache=_load_cache(),
        )
    return _app


def get_entra_token(scope: str) -> str:
    """Return a valid Entra access token for ``scope``.

    Tries the silent (cached/refresh) path first; on a miss it opens the system
    browser for an interactive sign-in. Calling this per request lets MSAL's
    silent refresh keep long-running agent loops authenticated. Raises
    :class:`EntraAuthError` on any failure so callers can fall back to
    tool-less behaviour.
    """
    if not is_configured():
        raise EntraAuthError(
            "Entra sign-in is not configured. Set ENTRA_TENANT_ID and "
            "ENTRA_CLIENT_ID in .env."
        )
    if not scope:
        raise EntraAuthError("No Entra scope given for token acquisition.")

    app = _get_app()
    scopes = [scope]

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])

    if not result:
        # No usable cached token — fall back to an interactive browser login.
        result = app.acquire_token_interactive(scopes, prompt="select_account")

    _save_cache()

    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description") or "no token returned"
        raise EntraAuthError(f"Entra sign-in failed: {detail}")

    return result["access_token"]


def sign_out() -> None:
    """Forget all cached accounts (next call triggers a fresh interactive login)."""
    app = _get_app()
    for account in app.get_accounts():
        app.remove_account(account)
    _save_cache()
