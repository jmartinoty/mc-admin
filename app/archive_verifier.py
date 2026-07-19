"""Vérification d'intégrité des archives — thread de fond (V5.x).

Une sauvegarde n'a de valeur que si elle est restaurable : ce thread relit
chaque archive en entier (tar + gzip, en flux) et persiste le verdict
(JsonArchiveChecks) que la page Sauvegardes affiche en badge.

Discipline de la famille des watchers (PlayerLogWatcher, HealthWatcher) :
observation passive, écrit directement dans son adapter — pas d'AdminService,
pas de RBAC (seule la LECTURE des verdicts passe par le service). Bornes :
UNE archive vérifiée par passe (une relecture = ~30 s de CPU sur le NAS pour
770 Mio, on lisse la charge), et jamais une archive plus jeune que
`min_age_seconds` (elle peut être encore en cours d'écriture par mc-backup).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from adapters.archive_validation import ArchiveInspection, inspect_archive
from domain.model import ArchiveCheck


def verify_archive(path) -> tuple[bool, str]:
    """Compatibilité : verdict d'intégrité historique sous forme de tuple."""
    inspection = inspect_archive(path)
    return inspection.ok, "" if inspection.ok else inspection.detail


class ArchiveVerifier:
    def __init__(self, archives, checks, poll_seconds: float = 60.0,
                 min_age_seconds: float = 180.0, sleep=None, clock=None, verify=None) -> None:
        self._archives = archives            # BackupArchivesPort (list + chemins)
        self._checks = checks                # JsonArchiveChecks
        self._poll_seconds = poll_seconds
        self._min_age = min_age_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._verify = verify or inspect_archive
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wait = sleep or self._stop.wait

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="archive-verifier")
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
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — le thread ne meurt jamais
                pass

    def tick(self) -> str | None:
        """Vérifie AU PLUS une archive (la plus récente non vérifiée d'abord —
        c'est celle qu'on restaurerait). Purge les verdicts d'archives disparues."""
        archives = sorted(self._archives.list_archives(), key=lambda a: a.created_at, reverse=True)
        self._checks.forget_missing({a.filename for a in archives})
        known = self._checks.statuses()
        now = self._clock()
        for archive in archives:
            check = known.get(archive.filename)
            if (
                check is not None
                and check.size_bytes == archive.size_bytes
                and check.archive_created_at == archive.created_at
                and check.restorable is not None
            ):
                continue  # déjà vérifiée, taille inchangée
            if (now - archive.created_at).total_seconds() < self._min_age:
                continue  # peut-être encore en cours d'écriture
            path = self._archives.archive_path(archive.filename)
            if path is None:
                continue
            # Marqueur « en cours » : la relecture prend ~30 s sur une grosse
            # archive, la page l'affiche pendant ce temps.
            self._checks.mark_checking(archive.filename)
            result = self._verify(path)
            if isinstance(result, ArchiveInspection):
                ok = result.ok
                detail = result.detail
                restorable = result.restorable
                world_name = result.world_name
            else:
                # Compatibilité avec les doubles de test historiques.
                ok, detail = result
                restorable = None
                world_name = ""
            self._checks.record(ArchiveCheck(
                filename=archive.filename, ok=ok, checked_at=now,
                size_bytes=archive.size_bytes, detail=detail,
                restorable=restorable, world_name=world_name,
                archive_created_at=archive.created_at,
            ))
            return archive.filename
        self._checks.mark_checking(None)  # rien à vérifier : pas de marqueur périmé
        return None
