"""Registre des sessions connectées (voir/révoquer les appareils).

Les cookies de session Starlette sont signés et AUTONOMES : sans état côté
serveur, une session ne peut jamais être invalidée avant son expiration. Ce
registre ajoute cet état minimal — un identifiant opaque (`sid`) par connexion
— pour rendre la révocation possible.

Placement : couche transport (`app/api/`), comme `login_security.py`. Le
domaine ignore les sessions HTTP ; ce n'est pas une règle métier mais un détail
d'authentification. Pas de nouvelle `Permission` : chacun gère SES appareils
(self-service, comme le changement de mot de passe) — `username` est exigé à
chaque opération, on ne révoque jamais la session d'autrui.

Persistance : `/data/sessions.json` (atomique, 0600 comme les autres secrets).
La copie en mémoire est autoritative pour la validation par requête (rapide,
sans I/O sous le polling) ; les mutations (connexion, déconnexion, révocation)
sont écrites en write-through. Conséquence VOULUE, distincte du choix « tout en
mémoire » d'autres états : une révocation SURVIT à un redéploiement de mc-admin
et personne n'est déconnecté en masse au redémarrage. Le « dernier accès » reste
en mémoire (best-effort, pur affichage) pour ne pas écrire à chaque requête.
"""
from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from adapters.atomic_json import atomic_write_json, load_json, shared_path_lock

# Aligné sur la durée de vie par défaut du cookie Starlette (14 jours) : au-delà,
# le cookie n'est plus envoyé, l'entrée serveur ne servirait plus qu'à encombrer.
_DEFAULT_TTL_SECONDS = 14 * 24 * 3600
# Garde-fou anti-gonflement : une session morte (onglet fermé sans logout) laisse
# une entrée jusqu'à son TTL. On borne par utilisateur, les plus anciennes tombent.
_DEFAULT_MAX_PER_USER = 50


@dataclass(frozen=True)
class SessionInfo:
    """Projection d'affichage d'une session (page « Appareils »)."""

    sid: str
    username: str
    created_at: float
    ip: str
    user_agent: str
    last_seen: float


class SessionRegistry:
    def __init__(
        self,
        path: str,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_per_user: int = _DEFAULT_MAX_PER_USER,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._lock = shared_path_lock(path)
        self._ttl = ttl_seconds
        self._max_per_user = max_per_user
        self._clock = clock
        self._sessions: dict[str, dict] = {}
        self._load_into_memory()

    # ---- chargement / persistance ----

    def _load_into_memory(self) -> None:
        with self._lock:
            raw = load_json(self._path, expected_type=dict, default_factory=dict)
            now = self._clock()
            clean: dict[str, dict] = {}
            for sid, record in raw.items():
                if not isinstance(sid, str) or not sid or not isinstance(record, dict):
                    continue
                username = record.get("username")
                if not isinstance(username, str) or not username:
                    continue
                try:
                    created = float(record.get("created_at") or 0.0)
                except (TypeError, ValueError):
                    continue
                if self._ttl and now - created > self._ttl:
                    continue  # session expirée : on ne la recharge pas
                clean[sid] = {
                    "username": username,
                    "created_at": created,
                    "ip": str(record.get("ip") or ""),
                    "user_agent": str(record.get("user_agent") or ""),
                    "last_seen": float(record.get("last_seen") or created),
                }
            self._sessions = clean

    def _persist(self) -> None:
        # Appelé sous _lock. 0600 : le sid est un identifiant de session (pas un
        # mot de passe, mais on ne l'expose pas plus que passwords.json).
        atomic_write_json(self._path, self._sessions, mode=0o600)

    def _enforce_cap(self, username: str) -> None:
        owned = [
            (sid, rec) for sid, rec in self._sessions.items()
            if rec["username"] == username
        ]
        if len(owned) <= self._max_per_user:
            return
        owned.sort(key=lambda item: item[1]["created_at"])
        for sid, _rec in owned[: len(owned) - self._max_per_user]:
            self._sessions.pop(sid, None)

    # ---- cycle de vie d'une session ----

    def register(self, username: str, ip: str, user_agent: str) -> str:
        """Ouvre une session et renvoie son `sid` (à poser dans le cookie)."""
        sid = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._sessions[sid] = {
                "username": username,
                "created_at": now,
                "ip": (ip or "")[:64],
                "user_agent": (user_agent or "")[:256],
                "last_seen": now,
            }
            self._enforce_cap(username)
            self._persist()
        return sid

    def is_valid(self, sid: str, username: str) -> bool:
        """Vrai si `sid` est une session vivante de `username`. Une session
        expirée est purgée à la volée. Une session absente = révoquée."""
        if not sid:
            return False
        with self._lock:
            record = self._sessions.get(sid)
            if record is None or record["username"] != username:
                return False
            if self._ttl and self._clock() - record["created_at"] > self._ttl:
                del self._sessions[sid]
                self._persist()
                return False
            return True

    def touch(self, sid: str) -> None:
        """Met à jour le « dernier accès » (mémoire seule : pas d'I/O par
        requête, l'info est purement cosmétique)."""
        with self._lock:
            record = self._sessions.get(sid)
            if record is not None:
                record["last_seen"] = self._clock()

    def list_for(self, username: str) -> list[SessionInfo]:
        """Sessions de `username`, la plus récemment active en tête."""
        with self._lock:
            infos = [
                SessionInfo(
                    sid=sid,
                    username=record["username"],
                    created_at=record["created_at"],
                    ip=record["ip"],
                    user_agent=record["user_agent"],
                    last_seen=record["last_seen"],
                )
                for sid, record in self._sessions.items()
                if record["username"] == username
            ]
        infos.sort(key=lambda info: info.last_seen, reverse=True)
        return infos

    def revoke(self, sid: str, username: str) -> bool:
        """Révoque UNE session de `username` (self-service). Renvoie False si
        elle n'existe pas ou appartient à quelqu'un d'autre."""
        with self._lock:
            record = self._sessions.get(sid)
            if record is None or record["username"] != username:
                return False
            del self._sessions[sid]
            self._persist()
            return True

    def revoke_others(self, keep_sid: str, username: str) -> int:
        """Révoque toutes les sessions de `username` SAUF `keep_sid` (« se
        déconnecter partout ailleurs »). Renvoie le nombre révoqué."""
        with self._lock:
            targets = [
                sid for sid, record in self._sessions.items()
                if record["username"] == username and sid != keep_sid
            ]
            for sid in targets:
                del self._sessions[sid]
            if targets:
                self._persist()
        return len(targets)
