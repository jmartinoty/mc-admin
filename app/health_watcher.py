"""Alerte « serveur down » — thread de fond (V5, désactivé par défaut).

Observe l'état du conteneur minecraft — et des conteneurs COMPAGNONS
déclarés (HEALTH_EXTRA_CONTAINERS, ex. le tunnel playit : resté mort 2 jours
sans que rien n'alerte, vécu le 17/07/2026) — via ContainerPort et notifie
chute et rétablissement de chacun. Observation PASSIVE (comme
PlayerLogWatcher) : pas une action utilisateur, donc pas d'AdminService/RBAC
— les ports sont lus directement.

Anti-bruit :
- il faut N sondes down CONSÉCUTIVES avant d'alerter (un `docker restart`
  manuel passe par un état arrêté transitoire qui ne doit pas alerter) ;
- une seule alerte par chute, puis une seule notification de rétablissement.

Activé par HEALTH_ALERTS=true (défaut : false — en dev, les redémarrages
fréquents spammeraient les canaux).
"""
from __future__ import annotations

import threading


class _Watched:
    """État anti-bruit d'UN conteneur surveillé (streak + alerte émise)."""

    def __init__(self, title: str, label: str, container, subject: str) -> None:
        self.title = title    # préfixe du titre de notification (« Serveur », « playit »)
        self.label = label    # sujet de la phrase du corps
        self.container = container
        self.subject = subject  # clé stable de l'incident ("server" ou nom du conteneur)
        self.down_streak = 0
        self.alerted = False


class HealthWatcher:
    def __init__(self, container, notifier, poll_seconds: float = 30.0,
                 down_polls_before_alert: int = 3,
                 watched_store=None, port_factory=None, incidents=None) -> None:
        # `container` = le serveur Minecraft ; les compagnons viennent du
        # STORE (watched_containers.json), relu à chaque sonde : un ajout
        # dans l'UI est effectif à la sonde suivante, sans redémarrage.
        # `port_factory(name) -> ContainerPort` construit l'accès à un
        # compagnon (DockerProxyContainer en prod, fake en test).
        self._server = _Watched("Serveur", "le serveur Minecraft", container, "server")
        self._store = watched_store
        self._port_factory = port_factory
        self._companions: dict[str, _Watched] = {}
        self._notifier = notifier
        self._incidents = incidents  # IncidentLogPort | None (best-effort)
        self._poll_seconds = poll_seconds
        self._threshold = down_polls_before_alert
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="health-watcher")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            interrupted = self._wait(self._poll_seconds)
            if interrupted or self._stop.is_set():
                break
            self._tick()

    def _tick(self) -> None:
        self._tick_one(self._server)
        for watched in self._companions_now():
            self._tick_one(watched)

    def _companions_now(self) -> list[_Watched]:
        """Compagnons du store, états anti-bruit conservés entre les sondes ;
        un retrait dans l'UI abandonne l'état (et donc l'alerte en cours)."""
        if self._store is None or self._port_factory is None:
            return []
        try:
            names = list(self._store.all())
        except Exception:  # noqa: BLE001 — store illisible : on garde l'existant
            return list(self._companions.values())
        for name in names:
            if name not in self._companions:
                self._companions[name] = _Watched(
                    name, f"le conteneur « {name} »", self._port_factory(name), name)
        for gone in set(self._companions) - set(names):
            del self._companions[gone]
        return [self._companions[name] for name in names]

    def _tick_one(self, watched: _Watched) -> None:
        try:
            running = watched.container.status().running
        except Exception:  # noqa: BLE001 — proxy injoignable : on ne conclut rien
            return
        if running:
            if watched.alerted:
                self._notifier.notify(
                    f"{watched.title} rétabli",
                    f"{watched.label.capitalize()} est de nouveau en ligne.", "info",
                    event="health")
                self._record_incident("close", watched.subject)
            watched.down_streak = 0
            watched.alerted = False
            return
        watched.down_streak += 1
        if watched.down_streak >= self._threshold and not watched.alerted:
            self._notifier.notify(
                f"{watched.title} down",
                f"{watched.label.capitalize()} est arrêté depuis "
                f"{int(watched.down_streak * self._poll_seconds)} s.",
                "error",
                event="health",
            )
            self._record_incident(
                "open", watched.subject, "availability", watched.title,
                f"{watched.title} arrêté")
            watched.alerted = True

    def _record_incident(self, action: str, subject: str, kind: str = "",
                         label: str = "", detail: str = "") -> None:
        """Persiste la transition (best-effort, comme la notification) : un
        journal d'incidents indisponible ne doit jamais casser la surveillance."""
        if self._incidents is None:
            return
        try:
            if action == "open":
                self._incidents.open(subject, kind, label, detail)
            else:
                self._incidents.close(subject)
        except Exception:  # noqa: BLE001 — best-effort, jamais bloquant
            pass
