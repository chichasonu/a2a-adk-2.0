"""Bring-your-own-key support. A visitor's keys arrive as request headers and live only for the
duration of that request (a ContextVar), so concurrent visitors can never see or spend each
other's keys. Keys are never written to disk, never logged, and never echoed back to the browser.
"""

import contextvars
import hashlib
import os
import re
from typing import Literal

KeyName = Literal["jev", "openrouter"]

_store: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("jev_user_keys", default=None)

ENV_VAR: dict[str, str] = {"jev": "TYPESAFE_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
HEADER: dict[str, str] = {"jev": "x-jev-key", "openrouter": "x-openrouter-key"}
KEY_NAMES: tuple[KeyName, ...] = ("jev", "openrouter")


def set_user_keys(keys: dict[str, str]) -> None:
    _store.set(keys)


def current_user_keys() -> dict[str, str]:
    return _store.get() or {}


def hosted() -> bool:
    """HOSTED=1: ignore the server's own keys entirely, so a public deployment can never spend
    the owner's money. Every visitor must bring their own keys; without them the demos run
    simulated."""
    return bool(re.match(r"^(1|true|yes)$", os.environ.get("HOSTED", ""), re.IGNORECASE))


_VALID = re.compile(r"^[\x21-\x7E]{8,400}$")


def valid_key(v: object) -> bool:
    """A key is only accepted if it looks like a real API key: printable ASCII, no spaces, sane
    length."""
    return isinstance(v, str) and bool(_VALID.match(v))


def key_for(name: str) -> str | None:
    user = (_store.get() or {}).get(name)
    if user:
        return user
    if hosted():
        return None
    return os.environ.get(ENV_VAR[name], "").strip() or None


def key_source(name: str) -> str:
    if (_store.get() or {}).get(name):
        return "you"
    return "server" if key_for(name) else "none"


def _fingerprint(key: str) -> str:
    """Stable, non-reversible id for a key, used to give each visitor's key its own budget."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def bucket(name: str) -> str:
    """Budget/availability bucket: all server-key traffic shares "server"; each user key gets its
    own."""
    user = (_store.get() or {}).get(name)
    return f"u:{_fingerprint(user)}" if user else "server"


def redact(message: str) -> str:
    """Replace any of the current request's keys in a string, so an upstream error can never echo
    one back."""
    out = message
    for k in (_store.get() or {}).values():
        if k:
            out = out.replace(k, "[your key]")
    return out
