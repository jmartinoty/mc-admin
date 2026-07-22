"""Alertes ressources — thread de fond (perf avancée, 17/07/2026).

Deux vigies dans une même boucle, chacune avec son anti-bruit :
- MSPT : moyenne au-dessus du seuil sur N sondes CONSÉCUTIVES -> « le
  serveur rame » (event `performance`), puis un seul rétablissement ;
- DISQUE : espace libre du volume des sauvegardes sous le seuil -> event
  `disk`, rétablissement au-delà du seuil + 20 % (hystérésis, pas de
  ping-pong autour de la limite).

Seuils RÉGLABLES dans l'UI (backlog fiabilité n° 4, 20/07/2026) : si un
`thresholds` (AlertThresholdsPort) est fourni, il est RELU à chaque passe
(coût négligeable, même pattern que StoreBackedNotifier) — un réglage change
donc au prochain poll, sans redémarrer mc-admin. Sans port (tests existants,
usage historique), les défauts ci-dessous et `mspt_polls_before_alert`
restent le seul comportement.

Même discipline que HealthWatcher : observation passive, ports lus en
direct, Prometheus injoignable = sonde non conclusive (on ne conclut rien).
Les envois sont filtrés PAR CANAL par le notifier (interrupteurs UI).
"""
from __future__ import annotations

import threading

_MSPT_THRESHOLD_MS = 50.0
_DISK_MIN_FREE_BYTES = 10 * 1024**3  # 10 Gio


class PerfWatcher:
    def __init__(self, metrics, archives, notifier, poll_seconds: float = 60.0,
                 mspt_polls_before_alert: int = 3, thresholds=None, incidents=None) -> None:
        self._metrics = metrics
        self._archives = archives
        self._notifier = notifier
        self._poll_seconds = poll_seconds
        self._threshold_polls = mspt_polls_before_alert
        self._thresholds = thresholds  # AlertThresholdsPort | None
        self._incidents = incidents    # IncidentLogPort | None (best-effort)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = self._stop.wait
        self._mspt_streak = 0
        self._mspt_alerted = False
        self._disk_alerted = False

    def _current_limits(self) -> tuple[float, float, int]:
        """(seuil MSPT ms, seuil disque octets, sondes avant alerte) — lu du
        port réglable s'il existe, sinon les défauts historiques."""
        if self._thresholds is None:
            return _MSPT_THRESHOLD_MS, _DISK_MIN_FREE_BYTES, self._threshold_polls
        try:
            values = self._thresholds.get()
        except Exception:  # noqa: BLE001 — config illisible : défauts, jamais un crash
            return _MSPT_THRESHOLD_MS, _DISK_MIN_FREE_BYTES, self._threshold_polls
        polls = max(1, round(values.mspt_sustained_minutes * 60 / self._poll_seconds))
        return values.mspt_threshold_ms, values.disk_min_free_gib * 1024**3, polls

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="perf-watcher")
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
        self._tick_mspt()
        self._tick_disk()

    def _tick_mspt(self) -> None:
        try:
            mspt = self._metrics.performance().mspt_mean
        except Exception:  # noqa: BLE001 — Prometheus injoignable : non conclusif
            return
        if mspt is None:
            return
        mspt_threshold, _disk_threshold, threshold_polls = self._current_limits()
        if mspt <= mspt_threshold:
            if self._mspt_alerted:
                self._notifier.notify("Lag serveur terminé",
                                      f"MSPT moyen revenu à {mspt:.1f} ms.",
                                      "info", event="performance")
                self._record_incident("close", "mspt")
            self._mspt_streak = 0
            self._mspt_alerted = False
            return
        self._mspt_streak += 1
        if self._mspt_streak >= threshold_polls and not self._mspt_alerted:
            minutes = int(self._mspt_streak * self._poll_seconds // 60)
            self._notifier.notify(
                "Lag serveur",
                f"MSPT moyen à {mspt:.1f} ms depuis ~{minutes} min (seuil : {mspt_threshold:.0f} ms — "
                "sous 20 TPS). La page Performances montre les entités en cause.",
                "warning", event="performance")
            self._record_incident("open", "mspt", "performance", "Lag serveur",
                                  f"MSPT {mspt:.0f} ms")
            self._mspt_alerted = True

    def _record_incident(self, action: str, subject: str, kind: str = "",
                         label: str = "", detail: str = "") -> None:
        """Persiste la transition (best-effort) : un journal d'incidents
        indisponible ne casse jamais la surveillance."""
        if self._incidents is None:
            return
        try:
            if action == "open":
                self._incidents.open(subject, kind, label, detail)
            else:
                self._incidents.close(subject)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _tick_disk(self) -> None:
        try:
            free = self._archives.free_bytes()
        except Exception:  # noqa: BLE001
            return
        if free is None:
            return
        _mspt_threshold, disk_threshold, _polls = self._current_limits()
        if free < disk_threshold:
            if not self._disk_alerted:
                self._notifier.notify(
                    "Espace disque bas",
                    f"{free / 1024**3:.1f} Gio libres sur le volume des sauvegardes "
                    f"(seuil : {disk_threshold / 1024**3:.0f} Gio).",
                    "warning", event="disk")
                self._record_incident("open", "disk", "disk", "Espace disque",
                                      f"{free / 1024**3:.1f} Gio libres")
                self._disk_alerted = True
        elif self._disk_alerted and free > disk_threshold * 1.2:
            self._notifier.notify("Espace disque rétabli",
                                  f"{free / 1024**3:.1f} Gio libres.", "info", event="disk")
            self._record_incident("close", "disk")
            self._disk_alerted = False
