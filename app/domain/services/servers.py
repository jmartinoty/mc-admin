"""Serveurs administrés, branchements, adresse/carte, compagnons, notifications.

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations

import re

from domain.errors import (
    InvalidGameValue,
    ServerUnavailable,
)
from domain.model import (
    AlertThresholds,
    ConnectionCheck,
    DetectedServer,
    Permission,
    ServerEntry,
    User,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.

from domain.services.base import (
    NOTIFICATION_CHANNEL_TYPES,
)


class ServersMixin:
    # ---- serveurs administrés & branchements (V6.3) ----

    _SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

    def active_server_container(self, user: User) -> str:
        """Conteneur réellement piloté par CETTE instance (fixé au démarrage —
        un serveur ajouté à chaud ne prend effet qu'après redémarrage)."""
        self._authorize(user, Permission.SERVER_MANAGE)
        return self._container_name

    def servers_list(self, user: User) -> list[ServerEntry]:
        self._authorize(user, Permission.SERVER_MANAGE)
        return self._servers.list_servers() if self._servers else []

    def discover_servers(self, user: User) -> list[DetectedServer]:
        """Conteneurs Minecraft repérés (configurés ou non) — lecture non
        auditée, best-effort."""
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._server_discovery is None:
            return []
        configured = {srv.container for srv in (self._servers.list_servers() if self._servers else [])}
        return self._server_discovery.discover(configured)

    def add_server(self, user: User, name: str, container: str,
                   rcon_host: str, rcon_port: int, world_dir: str = "world") -> ServerEntry:
        """Enregistre un serveur (assistant V6.3). Le premier enregistré
        devient le serveur piloté APRÈS redémarrage de mc-admin (les adapters
        sont construits au démarrage) ; les suivants attendent la V7."""
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._servers is None:
            raise InvalidGameValue("gestion des serveurs non configurée")
        name = name.strip()[:40]
        if not name:
            raise InvalidGameValue("nom de serveur vide")
        for label, value in (("conteneur", container.strip()), ("hôte RCON", rcon_host.strip())):
            if not self._SERVER_NAME_RE.match(value):
                raise InvalidGameValue(f"{label} invalide : {value!r}")
        if not (0 < int(rcon_port) < 65536):
            raise InvalidGameValue(f"port RCON invalide : {rcon_port}")
        from adapters.backup_profiles import slugify
        slug = slugify(name)
        existing = {srv.id for srv in self._servers.list_servers()}
        base, n = slug, 2
        while slug in existing:
            slug, n = f"{base}-{n}", n + 1
        entry = ServerEntry(
            id=slug, name=name, mode="docker", container=container.strip(),
            rcon_host=rcon_host.strip(), rcon_port=int(rcon_port),
            world_dir=world_dir.strip() or "world",
        )
        self._servers.add(entry)
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f'phase=server_added server={slug} name="{name}" container={entry.container}')
        return entry

    # ---- surveillance des compagnons (A2.1) ----

    def watched_containers(self, user: User) -> list[str]:
        """Liste des compagnons surveillés (page Surveillance)."""
        self._authorize(user, Permission.SERVER_MANAGE)
        return self._watched.all() if self._watched else []

    def watch_candidates(self, user: User) -> list[DetectedServer]:
        """Conteneurs de l'hôte proposables à la surveillance : tous, sauf
        ceux déjà surveillés et le serveur piloté (toujours surveillé)."""
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._server_discovery is None:
            return []
        excluded = set(self._watched.all() if self._watched else []) | {self._container_name}
        return [c for c in self._server_discovery.list_all() if c.name not in excluded]

    def watch_container(self, user: User, name: str) -> None:
        self._authorize(user, Permission.SERVER_MANAGE)
        name = name.strip()
        if self._watched is None:
            raise InvalidGameValue("surveillance non configurée")
        if not self._SERVER_NAME_RE.match(name):
            raise InvalidGameValue(f"nom de conteneur invalide : {name!r}")
        if name == self._container_name:
            raise InvalidGameValue("le serveur est déjà surveillé en permanence")
        if not self._watched.add(name):
            raise InvalidGameValue(f"« {name} » est déjà surveillé")
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=watch_added container={name}")

    def unwatch_container(self, user: User, name: str) -> None:
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._watched is None or not self._watched.remove(name):
            raise InvalidGameValue(f"« {name} » n'est pas surveillé")
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=watch_removed container={name}")

    def notification_channels(self, user: User) -> list[dict]:
        """Profils de canaux (page Notifications) — config comprise : la page
        d'édition en a besoin, et elle est réservée SERVER_MANAGE."""
        self._authorize(user, Permission.SERVER_MANAGE)
        return self._notification_config.list_channels() if self._notification_config else []

    def _validate_channel(self, ctype: str, config: dict) -> dict:
        if ctype not in NOTIFICATION_CHANNEL_TYPES:
            raise InvalidGameValue(f"type de canal inconnu : {ctype!r}")
        cleaned = {}
        for field in NOTIFICATION_CHANNEL_TYPES[ctype]:
            value = (config.get(field) or "").strip()
            if not value:
                raise InvalidGameValue(f"champ requis manquant : {field}")
            cleaned[field] = value
        if ctype == "discord" and not cleaned["webhook_url"].startswith("https://"):
            raise InvalidGameValue("l'URL du webhook Discord doit commencer par https://")
        return cleaned

    def add_notification_channel(self, user: User, ctype: str, label: str,
                                 config: dict, events: dict) -> str:
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._notification_config is None:
            raise InvalidGameValue("notifications non configurées côté serveur")
        cleaned = self._validate_channel(ctype, config)
        for existing in self._notification_config.list_channels():
            if existing["type"] == ctype and existing["config"] == cleaned:
                raise InvalidGameValue(
                    f"ce canal existe déjà (« {existing['label']} ») — double clic ?")
        label = (label or "").strip()[:40] or ctype
        channel_id = self._notification_config.add_channel(ctype, label, cleaned, events)
        # Jamais les secrets au journal — type/id/événements seulement.
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=notify_channel_added id={channel_id} type={ctype} "
                     f"events={','.join(k for k, v in events.items() if v) or 'aucun'}")
        return channel_id

    def update_notification_channel(self, user: User, channel_id: str, label=None,
                                    config=None, events=None, enabled=None) -> None:
        self._authorize(user, Permission.SERVER_MANAGE)
        existing = self._notification_config.get(channel_id) if self._notification_config else None
        if existing is None:
            raise InvalidGameValue(f"canal inconnu : {channel_id}")
        if config is not None:
            config = self._validate_channel(existing["type"], config)
        if label is not None:
            label = (label or "").strip()[:40] or existing["type"]
        self._notification_config.update_channel(
            channel_id, label=label, config=config, events=events, enabled=enabled)
        detail = f"phase=notify_channel_updated id={channel_id}"
        if enabled is not None:
            detail += f" enabled={enabled}"
        if events is not None:
            detail += f" events={','.join(k for k, v in events.items() if v) or 'aucun'}"
        self._record(user, Permission.SERVER_MANAGE, "allowed", detail)

    def remove_notification_channel(self, user: User, channel_id: str) -> None:
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._notification_config is None or not self._notification_config.remove_channel(channel_id):
            raise InvalidGameValue(f"canal inconnu : {channel_id}")
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=notify_channel_removed id={channel_id}")

    def record_notification_test(self, user: User, channel: str, ok: bool) -> None:
        """Trace du bouton « Envoyer un test » (l'envoi vit dans la couche API,
        qui construit le canal — le domaine n'importe pas les adapters)."""
        self._authorize(user, Permission.SERVER_MANAGE)
        self._record(user, Permission.SERVER_MANAGE, "allowed" if ok else "error",
                     f"phase=notify_test channel={channel} ok={ok}")


    _ADDRESS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:_]{0,63}$")

    def set_server_address(self, user: User, server_id: str, address: str) -> None:
        """Adresse PUBLIQUE à partager (UI-1 — retour Jeremy : « à aucun
        endroit on a l'IP ou le DNS du serveur »). Vide = effacer."""
        self._authorize(user, Permission.SERVER_MANAGE)
        address = (address or "").strip()
        if address and not self._ADDRESS_RE.match(address):
            raise InvalidGameValue(f"adresse invalide : {address!r}")
        if self._servers is None or not self._servers.set_address(server_id, address):
            raise InvalidGameValue(f"serveur inconnu : {server_id}")
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=server_address_updated server={server_id} address={address or '(effacée)'}")

    def server_address(self, user: User) -> str:
        """Adresse à afficher sur le tableau de bord — lecture, tous rôles."""
        self._authorize(user, Permission.STATUS)
        first = next(iter(self._servers.list_servers()), None) if self._servers else None
        return first.address if first else ""

    _MAP_URL_RE = re.compile(r"^https?://\S{1,200}$")

    @staticmethod
    def _clean_map_url(map_url: str) -> str:
        """Une URL collée depuis le navigateur embarque souvent la vue
        (#overworld:0:0:…) : on ne garde que la base — le fragment n'est
        jamais envoyé au serveur et casserait le collage des chemins du
        relais (cas réel constaté le 19/07/2026)."""
        return (map_url or "").strip().split("#", 1)[0].rstrip("/")

    def set_server_map_url(self, user: User, server_id: str, map_url: str) -> None:
        """URL de la carte web du monde (BlueMap, Dynmap…) — relayée par
        mc-admin sous sa propre origine (page Carte). Vide = effacer
        (l'entrée « Carte » disparaît)."""
        self._authorize(user, Permission.SERVER_MANAGE)
        map_url = self._clean_map_url(map_url)
        if map_url and not self._MAP_URL_RE.match(map_url):
            raise InvalidGameValue(f"URL de carte invalide : {map_url!r}")
        if self._servers is None or not self._servers.set_map_url(server_id, map_url):
            raise InvalidGameValue(f"serveur inconnu : {server_id}")
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=server_map_updated server={server_id} map_url={map_url or '(effacée)'}")

    def server_map_url(self, user: User) -> str:
        """URL de la carte à afficher (page Carte) — lecture, tous rôles."""
        self._authorize(user, Permission.STATUS)
        first = next(iter(self._servers.list_servers()), None) if self._servers else None
        return first.map_url if first else ""

    def test_map_url(self, user: User, map_url: str) -> tuple[bool, str]:
        """Teste EN DIRECT une adresse de carte (assistant page Carte).
        Verdict (ok, détail en langage utilisateur) — ne lève jamais pour un
        échec de test : un « ça ne répond pas » est un résultat, pas une
        erreur de mc-admin."""
        self._authorize(user, Permission.SERVER_MANAGE)
        map_url = self._clean_map_url(map_url)
        if not self._MAP_URL_RE.match(map_url):
            return False, "adresse invalide — elle doit commencer par http:// ou https://"
        if self._map_probe is None:
            return False, "test indisponible sur cette installation"
        ok, detail = self._map_probe.probe(map_url)
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=map_test url={map_url} ok={ok}")
        return ok, detail

    def connection_checks(self, user: User) -> list[ConnectionCheck]:
        """Teste EN DIRECT chaque branchement de l'instance (page Serveurs).
        Best-effort : un branchement cassé produit un ✗ expliqué, jamais une
        erreur — c'est le guide d'installation permanent."""
        self._authorize(user, Permission.SERVER_MANAGE)
        checks: list[ConnectionCheck] = []
        try:
            state = self._container.status()
            checks.append(ConnectionCheck(
                "container", "Conteneur du serveur", True,
                f"« {self._container_name} » {'en cours d’exécution' if state.running else 'arrêté'}"))
        except Exception as exc:  # noqa: BLE001
            checks.append(ConnectionCheck(
                "container", "Conteneur du serveur", False,
                f"injoignable via le proxy Docker — vérifier mc-socket-proxy et le nom du conteneur ({exc})"))
        try:
            players = self._game.list_players()
            checks.append(ConnectionCheck("rcon", "Console RCON", True,
                                          f"répond ({len(players)} joueur(s) en ligne)"))
        except Exception:  # noqa: BLE001
            checks.append(ConnectionCheck(
                "rcon", "Console RCON", False,
                "ne répond pas — vérifier RCON_HOST/RCON_PORT/RCON_PASSWORD et que le serveur est démarré"))
        try:
            lines = self._logs.recent(5)
            if lines:
                checks.append(ConnectionCheck("logs", "Logs du serveur", True, "fichier lisible"))
            else:
                checks.append(ConnectionCheck(
                    "logs", "Logs du serveur", False,
                    "aucune ligne lisible — vérifier le montage du fichier latest.log"))
        except Exception:  # noqa: BLE001
            checks.append(ConnectionCheck("logs", "Logs du serveur", False,
                                          "illisibles — vérifier le montage du fichier latest.log"))
        free = None
        try:
            free = self._backup_archives.free_bytes()
        except Exception:  # noqa: BLE001
            pass
        if free is not None:
            checks.append(ConnectionCheck("backups", "Volume des sauvegardes", True,
                                          f"{free // 1073741824} Go libres"))
        else:
            checks.append(ConnectionCheck("backups", "Volume des sauvegardes", False,
                                          "non monté — vérifier le volume /backups"))
        try:
            if self._profile_backup is not None:
                self._profile_backup.is_running()
                checks.append(ConnectionCheck("backup_tool", "Outil de sauvegarde", True,
                                              "conteneur de sauvegarde prêt"))
            else:
                checks.append(ConnectionCheck("backup_tool", "Outil de sauvegarde", False,
                                              "non configuré — profils de sauvegarde indisponibles"))
        except Exception:  # noqa: BLE001
            checks.append(ConnectionCheck(
                "backup_tool", "Outil de sauvegarde", False,
                "conteneur absent — le créer une fois : docker compose --profile tools create mc-backup-profile"))
        try:
            readings = self._metrics.snapshot()
            checks.append(ConnectionCheck("metrics", "Métriques (optionnel)", True,
                                          f"{len(readings)} métrique(s) suivies"))
        except Exception:  # noqa: BLE001
            checks.append(ConnectionCheck("metrics", "Métriques (optionnel)", False,
                                          "Prometheus injoignable — panneau de métriques indisponible"))
        if self._worker_integrity is not None:
            try:
                for worker in self._worker_integrity.statuses():
                    # ok=None (invérifiable) est montré en ✗ : sur cette page
                    # de diagnostic, « je ne sais pas » n'est pas un feu vert.
                    checks.append(ConnectionCheck(
                        f"worker_{worker.name}", worker.label,
                        worker.ok is True, worker.detail))
            except Exception:  # noqa: BLE001 — le diagnostic ne casse jamais la page
                checks.append(ConnectionCheck(
                    "workers", "Outils one-shot", False,
                    "état des outils illisible via le proxy Docker"))
        return checks

    def set_alert_thresholds(self, user: User, mspt_threshold_ms: float,
                             disk_min_free_gib: float, mspt_sustained_minutes: float) -> None:
        """Règle les seuils d'alerte de PerfWatcher (backlog fiabilité n° 4,
        owner). Bornes larges mais réelles : un seuil à 0 ou négatif rendrait
        le watcher inutile en silence plutôt que de lever une erreur."""
        self._authorize(user, Permission.SERVER_MANAGE)
        if self._alert_thresholds is None:
            raise ServerUnavailable("seuils d'alerte non configurés côté serveur")
        if not (0 < mspt_threshold_ms <= 1000):
            raise InvalidGameValue(f"seuil MSPT invalide : {mspt_threshold_ms}")
        if not (0 < disk_min_free_gib <= 100_000):
            raise InvalidGameValue(f"seuil disque invalide : {disk_min_free_gib}")
        if not (0 < mspt_sustained_minutes <= 120):
            raise InvalidGameValue(f"durée invalide : {mspt_sustained_minutes}")
        self._alert_thresholds.set(AlertThresholds(
            mspt_threshold_ms=mspt_threshold_ms,
            disk_min_free_gib=disk_min_free_gib,
            mspt_sustained_minutes=mspt_sustained_minutes,
        ))
        self._record(user, Permission.SERVER_MANAGE, "allowed",
                     f"phase=alert_thresholds_updated mspt_ms={mspt_threshold_ms} "
                     f"disk_gib={disk_min_free_gib} mspt_minutes={mspt_sustained_minutes}")

