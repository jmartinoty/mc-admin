"""Adapter : canaux de notification EN PROFILS (V2 — idée Jeremy 17/07).

`/data/notifications.json` porte une LISTE de canaux, chacun avec son type,
sa config, SES interrupteurs d'événements et un drapeau enabled — le calque
des profils de sauvegarde :

    {"version": 2,
     "channels": [{"id": "discord-copains", "type": "discord",
                   "label": "Discord copains",
                   "config": {"webhook_url": "https://…"},
                   "events": {"player": false, "health": true, "restore": true},
                   "enabled": true}]}

Migrations one-shot, silencieuses, à la lecture :
- schéma V1 (canaux en dict global + events globaux, A2.2) -> un profil par
  canal configuré, héritant des interrupteurs globaux ;
- variables d'env historiques (import_from_env) -> profils directement.

Le notifier RELIT ce fichier (cache mtime) : toute modif UI est effective au
prochain envoi. Les échecs d'actions (non tagués) partent TOUJOURS sur tous
les canaux actifs — non débrayable.
"""
from __future__ import annotations

import os

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.services import EVENT_KEYS, EVENT_LEGACY_DEFAULTS, NOTIFICATION_CHANNEL_TYPES

from .backup_profiles import slugify


def _norm_events(raw: dict | None) -> dict[str, bool]:
    raw = raw if isinstance(raw, dict) else {}
    return {k: bool(raw.get(k, EVENT_LEGACY_DEFAULTS.get(k, False))) for k in EVENT_KEYS}


def _norm_channel(raw: dict) -> dict | None:
    ctype = raw.get("type")
    if ctype not in NOTIFICATION_CHANNEL_TYPES:
        return None
    fields = NOTIFICATION_CHANNEL_TYPES[ctype]
    config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
    return {
        "id": str(raw.get("id") or slugify(str(raw.get("label") or ctype))),
        "type": ctype,
        "label": str(raw.get("label") or ctype),
        "config": {k: str(config.get(k) or "") for k in fields},
        "events": _norm_events(raw.get("events")),
        "enabled": bool(raw.get("enabled", True)),
    }


class JsonNotificationConfig:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)
        with self._lock:
            if os.path.isfile(path):
                os.chmod(path, 0o600)

    # ---- persistance + migrations de lecture ----

    @staticmethod
    def _valid_v2_channel(raw: dict) -> bool:
        ctype = raw.get("type")
        config = raw.get("config", {})
        events = raw.get("events", {})
        return (
            ctype in NOTIFICATION_CHANNEL_TYPES
            and (raw.get("id") is None or isinstance(raw.get("id"), str))
            and (raw.get("label") is None or isinstance(raw.get("label"), str))
            and isinstance(config, dict)
            and all(
                isinstance(config.get(field, ""), str)
                for field in NOTIFICATION_CHANNEL_TYPES[ctype]
            )
            and isinstance(events, dict)
            and all(
                key in EVENT_KEYS and isinstance(value, bool)
                for key, value in events.items()
            )
            and isinstance(raw.get("enabled", True), bool)
        )

    def _load(self, *, strict: bool = False) -> dict:
        data = load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )
        if (
            strict
            and "env_import_done" in data
            and not isinstance(data["env_import_done"], bool)
        ):
            raise CorruptJsonError(f"schéma JSON inattendu : {self._path}")
        channels = data.get("channels")
        if isinstance(channels, dict):  # schéma V1 (A2.2) -> profils
            channels = self._from_v1(channels, data.get("events"))
        elif channels is None:
            channels = []
        elif not isinstance(channels, list):
            if strict:
                raise CorruptJsonError(
                    f"schéma JSON inattendu : {self._path}"
                )
            channels = []
        out = []
        seen: set[str] = set()
        for raw in channels:
            if strict and (
                not isinstance(raw, dict)
                or not self._valid_v2_channel(raw)
            ):
                raise CorruptJsonError(
                    f"schéma JSON inattendu : {self._path}"
                )
            ch = _norm_channel(raw) if isinstance(raw, dict) else None
            if ch is None:
                if strict:
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    )
                continue
            base, n = ch["id"], 2
            if strict and ch["id"] in seen:
                raise CorruptJsonError(
                    f"identifiant de canal dupliqué : {self._path}"
                )
            while ch["id"] in seen:
                ch["id"], n = f"{base}-{n}", n + 1
            seen.add(ch["id"])
            out.append(ch)
        return {"channels": out, "env_import_done": bool(data.get("env_import_done", False))}

    @staticmethod
    def _from_v1(channels: dict, events) -> list[dict]:
        events = _norm_events(events if isinstance(events, dict) else {})
        out = []
        if channels.get("discord_webhook_url"):
            out.append({"id": "discord-importe", "type": "discord", "label": "Discord (importé)",
                        "config": {"webhook_url": channels["discord_webhook_url"]},
                        "events": events, "enabled": True})
        if channels.get("telegram_bot_token") and channels.get("telegram_chat_id"):
            out.append({"id": "telegram-importe", "type": "telegram", "label": "Telegram (importé)",
                        "config": {"bot_token": channels["telegram_bot_token"],
                                   "chat_id": channels["telegram_chat_id"]},
                        "events": events, "enabled": True})
        return out

    def _write(self, data: dict) -> None:
        atomic_write_json(
            self._path,
            {
                "version": 2,
                "channels": data["channels"],
                "env_import_done": data["env_import_done"],
            },
            ensure_ascii=False,
            indent=2,
            mode=0o600,
        )

    def mtime(self) -> float:
        try:
            return os.stat(self._path).st_mtime
        except OSError:
            return 0.0

    # ---- CRUD ----

    def list_channels(self) -> list[dict]:
        return self._load()["channels"]

    def get(self, channel_id: str) -> dict | None:
        return next((c for c in self.list_channels() if c["id"] == channel_id), None)

    def add_channel(self, type: str, label: str, config: dict, events: dict,
                    enabled: bool = True) -> str:
        with self._lock:
            data = self._load(strict=True)
            base = slugify(label or type) or type
            cid, n = base, 2
            existing = {c["id"] for c in data["channels"]}
            while cid in existing:
                cid, n = f"{base}-{n}", n + 1
            ch = _norm_channel({"id": cid, "type": type, "label": label,
                                "config": config, "events": events, "enabled": enabled})
            if ch is None:
                raise ValueError(f"type de canal inconnu : {type!r}")
            data["channels"].append(ch)
            self._write(data)
            return cid

    def update_channel(self, channel_id: str, label=None, config=None,
                       events=None, enabled=None) -> bool:
        with self._lock:
            data = self._load(strict=True)
            for ch in data["channels"]:
                if ch["id"] != channel_id:
                    continue
                if label is not None:
                    ch["label"] = str(label)
                if config is not None:
                    fields = NOTIFICATION_CHANNEL_TYPES[ch["type"]]
                    ch["config"] = {k: str(config.get(k) or "") for k in fields}
                if events is not None:
                    ch["events"] = _norm_events(events)
                if enabled is not None:
                    ch["enabled"] = bool(enabled)
                self._write(data)
                return True
            return False

    def remove_channel(self, channel_id: str) -> bool:
        with self._lock:
            data = self._load(strict=True)
            kept = [c for c in data["channels"] if c["id"] != channel_id]
            if len(kept) == len(data["channels"]):
                return False
            data["channels"] = kept
            self._write(data)
            return True

    # ---- amorçage env (one-shot) ----

    def import_from_env(self, channels: dict, events: dict) -> bool:
        with self._lock:
            data = self._load(strict=True)
            if data["env_import_done"]:
                return False
            has_content = any(channels.values()) or any(events.values())
            if not has_content and not os.path.exists(self._path):
                return False
            data["channels"].extend(self._from_v1(channels, events))
            data["env_import_done"] = True
            self._write(data)
            return True
