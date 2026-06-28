"""Downstream-identity seam — quarantines the identity provider (Entra).

The whole app asks for downstream credentials through one function:

    mint_downstream_token(system) -> str   # a bearer token for that system

so the orchestrator, gateway, and MCP servers never name MSAL or Entra. To swap
identity providers, or to move token-minting into a central service later, only
this module (and :mod:`auth_entra` behind it) changes.

Identity model (locked for this pass): a per-user *delegated* token is acquired
for the target system's app-registration scope; that system's MCP server does
the On-Behalf-Of exchange to its own backend. So minting a token = picking the
right scope and asking Entra for it. There is no standing/broad credential and
no service account here.

Per-system scopes come from the environment so adding a system is config, not
code:  ``ENTRA_SCOPE_FINANCE``, ``ENTRA_SCOPE_HR``, … (the legacy ``ENTRA_SCOPE``
is still honoured for finance for backward compatibility).
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    from paths import app_base_dir

    load_dotenv(app_base_dir() / ".env")
except ImportError:
    pass


class IdentityError(RuntimeError):
    """Raised when a downstream token cannot be minted for a system."""


# Per-system delegated scopes. Finance falls back to the legacy ENTRA_SCOPE so
# existing .env files keep working. New systems add an ``ENTRA_SCOPE_<SYS>`` key.
_SYSTEM_SCOPES: dict[str, str] = {
    "finance": os.getenv("ENTRA_SCOPE_FINANCE", "") or os.getenv("ENTRA_SCOPE", ""),
    # "hr": os.getenv("ENTRA_SCOPE_HR", ""),       # uncomment when HR comes online
    # "project": os.getenv("ENTRA_SCOPE_PROJECT", ""),
    # "crm": os.getenv("ENTRA_SCOPE_CRM", ""),
}


def scope_for(system: str) -> str:
    """The configured Entra scope for a system (empty string if unset)."""
    return _SYSTEM_SCOPES.get(system, "")


def configured_systems() -> list[str]:
    """Systems that have both Entra base config and a scope set."""
    try:
        import auth_entra

        base_ok = auth_entra.is_configured()
    except Exception:
        base_ok = False
    if not base_ok:
        return []
    return [sys for sys, scope in _SYSTEM_SCOPES.items() if scope]


def is_configured(system: str) -> bool:
    """True when a downstream token can be minted for ``system``."""
    return system in configured_systems()


def mint_downstream_token(system: str, *, user: str | None = None,
                          scopes: list[str] | None = None) -> str:
    """Return a per-user bearer token for ``system``.

    ``user`` is accepted for forward-compatibility (a future central service
    mints on behalf of a named user); on the desktop the signed-in MSAL account
    is implicit. ``scopes`` may override the configured scope. Raises
    :class:`IdentityError` on any failure so callers can degrade gracefully.
    """
    scope = (scopes[0] if scopes else "") or scope_for(system)
    if not scope:
        raise IdentityError(f"No Entra scope configured for system '{system}'.")
    try:
        import auth_entra

        return auth_entra.get_entra_token(scope)
    except IdentityError:
        raise
    except Exception as exc:  # EntraAuthError, import error, etc.
        raise IdentityError(str(exc)) from exc
