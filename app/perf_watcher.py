"""Alertes ressources — thread de fond (perf avancée, 17/07/2026).

Deux vigies dans une même boucle, chacune avec son anti-bruit :
- MSPT : moyenne au-dessus de 50 ms sur N sondes CONSÉCUTIVES -> « le
  serveur rame » (event `performance`), puis un seul rétablissement ;
- DISQUE : espace libre du volume des sauvegardes sous le seuil -> event
  `disk`, rétablissement au-delà du seuil + 20 % (hystérésis, pas de
  ping-pong autour de la limite).

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
                 mspt_polls_before_alert: int = 3) -> None:
        self._metrics = metrics
        self._archives = archives
        self._notifier = notifier
        self._poll_seconds = poll_seconds
        self._threshold_polls = mspt_polls_before_alert
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = self._stop.wait
        self._mspt_streak = 0
        self._mspt_alerted = False
        self._disk_alerted = False

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
        if mspt <= _MSPT_THRESHOLD_MS:
            if self._mspt_alerted:
                self._notifier.notify("Lag serveur terminé",
                                      f"MSPT moyen revenu à {mspt:.1f} ms.",
                                      "info", event="performance")
            self._mspt_streak = 0
            self._mspt_alerted = False
            return
        self._mspt_streak += 1
        if self._mspt_streak >= self._threshold_polls and not self._mspt_alerted:
            minutes = int(self._mspt_streak * self._poll_seconds // 60)
            self._notifier.notify(
                "Lag serveur",
                f"MSPT moyen à {mspt:.1f} ms depuis ~{minutes} min (seuil : 50 ms — "
                "sous 20 TPS). La page Performances montre les entités en cause.",
                "warning", event="performance")
            self._mspt_alerted = True

    def _tick_disk(self) -> None:
        try:
            free = self._archives.free_bytes()
        except Exception:  # noqa: BLE001
            return
        if free is None:
            return
        if free < _DISK_MIN_FREE_BYTES:
            if not self._disk_alerted:
                self._notifier.notify(
                    "Espace disque bas",
                    f"{free / 1024**3:.1f} Gio libres sur le volume des sauvegardes "
                    f"(seuil : {_DISK_MIN_FREE_BYTES // 1024**3} Gio).",
                    "warning", event="disk")
                self._disk_alerted = True
        elif self._disk_alerted and free > _DISK_MIN_FREE_BYTES * 1.2:
            self._notifier.notify("Espace disque rétabli",
                                  f"{free / 1024**3:.1f} Gio libres.", "info", event="disk")
            self._disk_alerted = False
