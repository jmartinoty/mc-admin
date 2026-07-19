"""Adapters NotificationPort : Discord (webhook) + Telegram (Bot API).

`CompositeNotifier` est la SEULE implémentation branchée sur le domaine : elle
combine 0..N canaux et garantit le contrat « ne lève jamais » en isolant
chaque canal (un webhook Discord en panne n'empêche pas Telegram de partir, et
aucune des deux n'interrompt jamais l'action en cours dans AdminService).
Avec zéro canal configuré, `CompositeNotifier([])` est un no-op silencieux —
même politique que `NotConfiguredBackup`.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

_log = logging.getLogger("mc-admin.notifications")

# Couleurs d'embed Discord (decimal) selon le niveau.
_DISCORD_COLORS = {"info": 0x2F6FEB, "warning": 0xC9A227, "error": 0x8A2B2B}

# Cloudflare (devant les webhooks Discord) REFUSE le User-Agent par défaut de
# Python (« Python-urllib/3.x ») : HTTP 403 « error code: 1010 », constaté en
# réel le 13/07/2026. Un UA identifiant l'application suffit.
_HEADERS = {"Content-Type": "application/json", "User-Agent": "mc-admin (notifier auto-hébergé)"}


class DiscordWebhookNotifier:
    def __init__(self, webhook_url: str, timeout: float = 5.0) -> None:
        self._url = webhook_url
        self._timeout = timeout

    def notify(self, title: str, message: str, level: str = "info", event: str = "") -> None:
        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": _DISCORD_COLORS.get(level, _DISCORD_COLORS["info"]),
                }
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._url, data=body, headers=_HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout):  # noqa: S310 — URL de config utilisateur
            pass


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout

    def notify(self, title: str, message: str, level: str = "info", event: str = "") -> None:
        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🛑"}.get(level, "ℹ️")
        text = f"{emoji} *{title}*\n{message}"
        body = json.dumps(
            {"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"}
        ).encode("utf-8")
        req = urllib.request.Request(self._url, data=body, headers=_HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout):  # noqa: S310
            pass


class CompositeNotifier:
    """Fan-out best-effort : chaque canal est isolé, aucune exception ne remonte."""

    def __init__(self, notifiers: list) -> None:
        self._notifiers = list(notifiers)

    def notify(self, title: str, message: str, level: str = "info", event: str = "") -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify(title, message, level)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                # Best-effort : un canal en panne n'affecte ni l'action ni les
                # autres canaux — mais l'échec est LOGGUÉ (un 403 silencieux a
                # caché pendant une journée un webhook bloqué par Cloudflare).
                detail = f"HTTP {exc.code} {exc.read()[:120]!r}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
                _log.warning("notification perdue (%s, %r) : %s",
                             type(notifier).__name__, title, detail)


def build_channel(ctype: str, config: dict):
    """Construit UN canal depuis son profil (V2) — None si config incomplète."""
    if ctype == "discord" and config.get("webhook_url"):
        return DiscordWebhookNotifier(config["webhook_url"])
    if ctype == "telegram" and config.get("bot_token") and config.get("chat_id"):
        return TelegramNotifier(config["bot_token"], config["chat_id"])
    return None


class StoreBackedNotifier:
    """NotificationPort adossé à notifications.json (V2 : canaux en profils).

    Relit la config quand le fichier change (cache mtime) : canal ajouté,
    suspendu ou interrupteur basculé dans l'UI = effectif au prochain envoi,
    sans redémarrage. Le filtrage est PAR CANAL : une notification taguée
    (player/health/restore) ne part que vers les canaux actifs dont
    l'interrupteur correspondant est ouvert ; sans tag (échecs d'actions),
    elle part vers TOUS les canaux actifs."""

    def __init__(self, store) -> None:
        self._store = store
        self._mtime = -1.0
        self._targets: list[tuple[object, dict]] = []  # (canal, events)

    def _refresh(self) -> None:
        mtime = self._store.mtime()
        if mtime == self._mtime:
            return
        self._mtime = mtime
        targets = []
        for ch in self._store.list_channels():
            if not ch.get("enabled", True):
                continue
            notifier = build_channel(ch["type"], ch["config"])
            if notifier is not None:
                targets.append((notifier, ch.get("events") or {}))
        self._targets = targets

    def notify(self, title: str, message: str, level: str = "info", event: str = "") -> None:
        try:
            self._refresh()
        except Exception:  # noqa: BLE001 — config illisible : on garde la précédente
            pass
        for notifier, events in self._targets:
            if event and not events.get(event, False):
                continue
            CompositeNotifier([notifier]).notify(title, message, level)


def telegram_detect_chats(bot_token: str, timeout: float = 6.0) -> list[dict]:
    """Chats vus par le bot (getUpdates) — l'assistant s'en sert pour
    préremplir le chat ID (piège n° 1 de Telegram, vécu le 17/07/2026).
    Renvoie [{id, name, type}], dédupliqué, plus récent d'abord.
    Lève urllib.error.HTTPError telle quelle (401 = jeton invalide — le
    route la traduit)."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    with urllib.request.urlopen(url, timeout=timeout) as res:  # noqa: S310
        data = json.load(res)
    chats: dict[int, dict] = {}
    for update in data.get("result") or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("id") is None:
                continue
            chats[chat["id"]] = {
                "id": str(chat["id"]),
                "name": chat.get("first_name") or chat.get("title") or chat.get("username") or "?",
                "type": {"private": "privé", "group": "groupe", "supergroup": "groupe",
                         "channel": "canal"}.get(chat.get("type"), chat.get("type") or "?"),
            }
    return list(reversed(list(chats.values())))
