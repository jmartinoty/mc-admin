"""TOTP (RFC 6238) en stdlib — deuxième facteur d'authentification.

Doctrine maison « stdlib quand possible » (comme le RCON et le portier) : pas de
dépendance `pyotp`, tout tient dans `hmac`/`hashlib`/`struct`/`base64`/`secrets`.

Fonctions PURES et testables : la génération/vérification ne connaît ni le
stockage (adapter `JsonTotp`) ni le flux HTTP. Le secret est une clé base32
(compatible Google Authenticator / Aegis / FreeOTP), 6 chiffres, période 30 s,
HMAC-SHA1 — les valeurs par défaut universelles des applications d'auth.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_PERIOD = 30
_DIGITS = 6
_SECRET_BYTES = 20  # 160 bits, recommandation RFC 4226


def generate_secret() -> str:
    """Nouvelle clé base32 sans remplissage (format saisi/scanné par les apps)."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes | None:
    cleaned = (secret or "").strip().replace(" ", "").upper()
    if not cleaned:
        return None
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding, casefold=True)
    except (ValueError, base64.binascii.Error):
        return None


def _hotp(key: bytes, counter: int) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** _DIGITS)).zfill(_DIGITS)


def code_at(secret: str, moment: float) -> str | None:
    """Code TOTP attendu à l'instant `moment` (epoch). None si secret illisible."""
    key = _decode_secret(secret)
    if key is None:
        return None
    return _hotp(key, int(moment) // _PERIOD)


def verify(secret: str, code: str, *, at: float | None = None, window: int = 1) -> bool:
    """Vrai si `code` est valide pour `secret`. Tolère `window` pas de 30 s de
    part et d'autre (décalage d'horloge) ; comparaison en temps constant."""
    key = _decode_secret(secret)
    if key is None:
        return False
    candidate = (code or "").strip()
    if len(candidate) != _DIGITS or not candidate.isdigit():
        return False
    counter = int(time.time() if at is None else at) // _PERIOD
    for drift in range(-window, window + 1):
        expected = _hotp(key, counter + drift)
        if hmac.compare_digest(expected, candidate):
            return True
    return False


def provisioning_uri(secret: str, account: str, issuer: str = "mc-admin") -> str:
    """URI otpauth:// pour un QR code ou l'ajout manuel dans l'app d'auth."""
    # Label standard « issuer:account » : chaque partie encodée, le ':' conservé.
    label = f"{quote(issuer)}:{quote(account)}"
    params = (
        f"secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={_DIGITS}&period={_PERIOD}"
    )
    return f"otpauth://totp/{label}?{params}"


def format_secret(secret: str) -> str:
    """Secret en groupes de 4 pour une saisie manuelle plus sûre."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
