"""Session + magic-link signing shared between the bridge and the web UI.

One module so bridge.py, web.py, and bot.py all use the same key + salt
discipline. Session cookies encode a role string:

    operator        — full access; set after /ui/login with BRIDGE_API_KEY.
    tenant:<N>      — scoped access; set after /ui/auth?token=<magic-link>.

Magic links use a separate salt and a short max_age (5 min) so a leaked
session cookie can't be replayed as a magic link and vice versa.
"""

from __future__ import annotations

import os
from typing import Optional

from itsdangerous import BadSignature, TimestampSigner

COOKIE_NAME = "gradphone_session"
COOKIE_MAX_AGE = 7 * 24 * 3600  # one week
MAGIC_MAX_AGE = 5 * 60  # five minutes

_SESSION_SALT = "gradphone.ui"
_MAGIC_SALT = "gradphone.magic"


_MIN_KEY_LEN = 16


def _key() -> str:
    """The signing secret for session cookies and magic links.

    Fails closed: there is no built-in default. ``BRIDGE_API_KEY`` must be set
    to a non-trivial value, otherwise an attacker who knows the (open-source)
    default could forge an operator cookie. Set it in your local ``.env``.
    """
    key = os.environ.get("BRIDGE_API_KEY", "").strip()
    if len(key) < _MIN_KEY_LEN:
        raise RuntimeError(
            "BRIDGE_API_KEY must be set to at least "
            f"{_MIN_KEY_LEN} characters (generate one with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(36))'`)."
        )
    return key


def session_signer() -> TimestampSigner:
    return TimestampSigner(_key(), salt=_SESSION_SALT)


def magic_signer() -> TimestampSigner:
    return TimestampSigner(_key(), salt=_MAGIC_SALT)


# ─── Session role helpers ─────────────────────────────────

def encode_role(role: str) -> str:
    return session_signer().sign(role.encode()).decode("ascii")


def decode_role(cookie_value: Optional[str]) -> Optional[str]:
    if not cookie_value:
        return None
    try:
        return session_signer().unsign(cookie_value, max_age=COOKIE_MAX_AGE).decode("ascii")
    except BadSignature:
        return None


def role_tenant_id(role: Optional[str]) -> Optional[int]:
    """Return the tenant_id from a role string, or None for operator/unauth."""
    if not role or not role.startswith("tenant:"):
        return None
    try:
        return int(role.split(":", 1)[1])
    except ValueError:
        return None


def is_operator(role: Optional[str]) -> bool:
    return role == "operator"


# ─── Magic-link helpers ───────────────────────────────────

def make_magic_token(tenant_id: int) -> str:
    payload = f"tenant:{tenant_id}".encode()
    return magic_signer().sign(payload).decode("ascii")


def verify_magic_token(token: str) -> Optional[str]:
    """Return the role string ('tenant:N') on success, None on failure."""
    if not token:
        return None
    try:
        return magic_signer().unsign(token, max_age=MAGIC_MAX_AGE).decode("ascii")
    except BadSignature:
        return None
