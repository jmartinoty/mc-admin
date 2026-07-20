"""Sauvegardes : profils, archives, progression, restauration transactionnelle, rétention.

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations

from datetime import date, timedelta

from domain.errors import (
    BackupArchiveNotFound,
    BackupUnavailable,
    InvalidGameValue,
    RestoreUnavailable,
)
from domain.model import (
    BackupArchive,
    BackupOutcome,
    BackupProfile,
    BackupProgress,
    BackupRetentionItem,
    BackupRetentionPlan,
    BackupResult,
    ArchiveCheck,
    PendingRestore,
    Permission,
    RestoreOperation,
    RestoreProgress,
    StorageSnapshot,
    User,
)
from domain.ports import (
    BackupPort,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.

from domain.services.base import (
    _BACKUP_WATCH_USER,
)


def _project_saturation(history: list[StorageSnapshot]) -> date | None:
    """Projection LINÉAIRE simple sur l'espace libre : au rythme actuel de
    remplissage, quand le volume sera-t-il plein ? Une tendance plate ou en
    amélioration (rétention qui suit le rythme, ménage manuel) n'a pas de
    date honnête à donner — None plutôt qu'une extrapolation absurde."""
    usable = [p for p in history if p.free_bytes is not None]
    if len(usable) < 2:
        return None
    first, last = usable[0], usable[-1]
    span_days = (date.fromisoformat(last.date) - date.fromisoformat(first.date)).days
    if span_days <= 0:
        return None
    slope_per_day = (last.free_bytes - first.free_bytes) / span_days
    if slope_per_day >= 0:
        return None
    days_left = last.free_bytes / -slope_per_day
    return date.fromisoformat(last.date) + timedelta(days=days_left)


class BackupsMixin:
    # ---- profils de sauvegarde (V6) ----

    _PROFILE_HOURS_ALLOWED = (0, 6, 12, 24, 48)
    # « mods_jars » : alias hérité accepté en entrée, plus proposé par l'UI.
    _PROFILE_CATEGORIES = ("world", "mods", "mods_jars", "config", "admin", "all")

    def backup_profiles_list(self, user: User) -> list[BackupProfile]:
        """Profils configurés (lecture non auditée)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        if self._backup_profiles is None:
            return []
        return self._backup_profiles.list_profiles()

    def create_backup_profile(self, user: User, name: str, include: tuple[str, ...],
                              hours: float = 0.0, daily_at: str | None = None,
                              retention_daily_days: int = 0,
                              retention_monthly_months: int = 0) -> BackupProfile:
        """Crée un profil (owner). Les catégories viennent d'une liste FERMÉE,
        la destination est DÉRIVÉE (profiles/<slug>) — jamais un chemin libre."""
        self._authorize(user, Permission.BACKUP_MANAGE)
        if self._backup_profiles is None:
            raise BackupUnavailable("profils de sauvegarde non configurés")
        name = name.strip()[:40]
        if not name:
            raise InvalidGameValue("nom de profil vide")
        include = tuple(c for c in include if c in self._PROFILE_CATEGORIES)
        if not include:
            raise InvalidGameValue("aucune catégorie valide sélectionnée")
        hours, daily_at = self._validate_profile_schedule(hours, daily_at)
        from adapters.backup_profiles import slugify
        slug = slugify(name)
        existing = {p.id for p in self._backup_profiles.list_profiles()}
        base, n = slug, 2
        while slug in existing:
            slug, n = f"{base}-{n}", n + 1
        profile = BackupProfile(
            id=slug, name=name, include=include, dest=f"profiles/{slug}",
            hours=hours, daily_at=daily_at,
            retention_daily_days=max(0, int(retention_daily_days)),
            retention_monthly_months=max(0, int(retention_monthly_months)),
        )
        self._backup_profiles.upsert(profile)
        self._record(user, Permission.BACKUP_MANAGE, "allowed",
                     f'phase=profile_created profile={slug} name="{name}" include={",".join(include)}')
        return profile

    def _validate_profile_schedule(self, hours: float, daily_at: str | None):
        if daily_at:
            daily_at = daily_at.strip()
            parts = daily_at.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts) \
                    or not (0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60):
                raise InvalidGameValue(f"heure invalide : {daily_at}")
            return 0.0, daily_at
        if hours not in self._PROFILE_HOURS_ALLOWED:
            raise InvalidGameValue(f"intervalle invalide : {hours}")
        return float(hours), None

    def update_backup_profile(self, user: User, profile_id: str, name: str,
                              include: tuple[str, ...], hours: float = 0.0,
                              daily_at: str | None = None,
                              retention_daily_days: int = 0,
                              retention_monthly_months: int = 0) -> BackupProfile:
        self._authorize(user, Permission.BACKUP_MANAGE)
        current = self._backup_profiles.get(profile_id) if self._backup_profiles else None
        if current is None:
            raise BackupArchiveNotFound(f"profil inconnu : {profile_id}")
        name = name.strip()[:40] or current.name
        include = tuple(c for c in include if c in self._PROFILE_CATEGORIES) or current.include
        hours, daily_at = self._validate_profile_schedule(hours, daily_at)
        profile = BackupProfile(
            id=current.id, name=name, include=include, dest=current.dest,
            hours=hours, daily_at=daily_at,
            retention_daily_days=max(0, int(retention_daily_days)),
            retention_monthly_months=max(0, int(retention_monthly_months)),
            enabled=current.enabled, last_run=current.last_run,
        )
        self._backup_profiles.upsert(profile)
        self._record(user, Permission.BACKUP_MANAGE, "allowed",
                     f'phase=profile_updated profile={current.id} name="{name}"')
        return profile

    def toggle_backup_profile(self, user: User, profile_id: str, enabled: bool) -> None:
        self._authorize(user, Permission.BACKUP_MANAGE)
        current = self._backup_profiles.get(profile_id) if self._backup_profiles else None
        if current is None:
            raise BackupArchiveNotFound(f"profil inconnu : {profile_id}")
        self._backup_profiles.upsert(BackupProfile(
            id=current.id, name=current.name, include=current.include, dest=current.dest,
            hours=current.hours, daily_at=current.daily_at,
            retention_daily_days=current.retention_daily_days,
            retention_monthly_months=current.retention_monthly_months,
            enabled=enabled, last_run=current.last_run,
        ))
        self._record(user, Permission.BACKUP_MANAGE, "allowed",
                     f"phase=profile_{'resumed' if enabled else 'suspended'} profile={current.id}")

    def delete_backup_profile(self, user: User, profile_id: str) -> None:
        """Supprime le PROFIL — jamais ses archives (elles restent listées)."""
        self._authorize(user, Permission.BACKUP_MANAGE)
        try:
            running = self._profile_backup is not None and self._profile_backup.is_running()
        except Exception:  # noqa: BLE001 — état inconnu : ne bloque pas la suppression
            running = False
        # Retour Jeremy 18/07 : seul le profil DONT la sauvegarde tourne est
        # verrouillé. Identité du profil actif inconnue (app redémarrée en
        # cours de sauvegarde) = prudence, on bloque.
        if running and self._active_profile_id in (None, profile_id):
            raise BackupUnavailable(
                "la sauvegarde de cette configuration est en cours : réessayer ensuite"
            )
        if self._backup_profiles is None or not self._backup_profiles.delete(profile_id):
            raise BackupArchiveNotFound(f"profil inconnu : {profile_id}")
        self._record(user, Permission.BACKUP_MANAGE, "allowed",
                     f"phase=profile_deleted profile={profile_id}")

    def trigger_profile_backup(self, user: User, profile_id: str) -> BackupResult:
        """Déclenche la sauvegarde d'un profil via le conteneur générique."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        with self._backup_restore_lock:
            return self._trigger_profile_backup_locked(user, profile_id)

    def _trigger_profile_backup_locked(
        self,
        user: User,
        profile_id: str,
    ) -> BackupResult:
        if self._restore_in_progress():
            raise BackupUnavailable(
                "sauvegarde indisponible pendant une restauration"
            )
        try:
            if self._restore_safety_backup.is_running():
                raise BackupUnavailable(
                    "sauvegarde indisponible pendant la sauvegarde de sécurité"
                )
        except BackupUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - état inconnu : aucun départ concurrent
            raise BackupUnavailable(
                f"état de la sauvegarde de sécurité illisible : {exc}"
            ) from exc
        profile = self._backup_profiles.get(profile_id) if self._backup_profiles else None
        if profile is None or self._profile_backup is None:
            raise BackupUnavailable(f"profil inconnu ou non configuré : {profile_id}")
        # Désalignements CONFIRMÉS seulement (image/réseau périmé) : un check
        # invérifiable ne bloque pas une sauvegarde — elle échouerait d'elle-même
        # avec sa propre erreur si le proxy est réellement muet.
        if self._worker_integrity is not None:
            worker_issues = self._worker_integrity.issues("backup")
            if worker_issues:
                raise BackupUnavailable("; ".join(worker_issues))
        from adapters.backup_profiles import includes_for
        includes, excludes = includes_for(profile.include, self._world_dir)
        try:
            result = self._profile_backup.trigger_profile(
                dest=profile.dest, name=f"mc-{profile.id}",
                includes=includes, excludes=excludes,
            )
        except Exception as exc:  # noqa: BLE001
            self._record(user, Permission.BACKUP_TRIGGER, "error", str(exc))
            self._notify.notify("Sauvegarde échouée", str(exc), "error")
            raise
        if result.started or profile.id not in self._backup_started_at:
            now = self._clock.now()
            self._active_profile_id = profile.id
            self._backup_started_at[profile.id] = now
            self._backup_progress_max.pop(profile.id, None)
            self._backup_result = None
            self._backup_operation_ids[profile.id] = f"backup::{profile.id}::{now.isoformat()}"
        self._record(user, Permission.BACKUP_TRIGGER, "allowed",
                     f'phase=backup_started kind={profile.id} profile="{profile.name}" '
                     f"requested_by={user.username} "
                     f"operation_id={self._backup_operation_ids.get(profile.id, '')}")
        return result

    def tick_backup_profiles(self, system_user: User) -> None:
        """Déclenche les profils planifiés arrivés à échéance (BackupScheduler).
        Même logique d'échéance que la planification V5 (intervalle depuis
        last_run, OU heure quotidienne locale avec rattrapage le jour même)."""
        self._authorize(system_user, Permission.BACKUP_TRIGGER)
        with self._backup_restore_lock:
            self._tick_backup_profiles_locked(system_user)

    def _tick_backup_profiles_locked(self, system_user: User) -> None:
        if self._backup_profiles is None or self._profile_backup is None:
            return
        if self._restore_in_progress():
            return
        try:
            if self._restore_safety_backup.is_running():
                return
        except Exception:  # noqa: BLE001 - état inconnu : ne pas avancer le planning
            return
        now = self._clock.now()
        for profile in self._backup_profiles.list_profiles():
            if not profile.enabled:
                continue
            if not self._profile_due(profile, now):
                continue
            self._backup_profiles.mark_run(profile.id, now)  # AVANT : pas de rafale si erreur
            try:
                self._trigger_profile_backup_locked(system_user, profile.id)
            except Exception:  # noqa: BLE001 — déjà audité ; un profil en panne ne bloque pas les autres
                continue

    def _profile_due(self, profile: BackupProfile, now) -> bool:
        if profile.daily_at:
            local_now = now.astimezone()
            if profile.last_run is not None and profile.last_run.astimezone().date() == local_now.date():
                return False
            hour, minute = (int(part) for part in profile.daily_at.split(":"))
            return local_now >= local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if profile.hours <= 0:
            return False
        if profile.last_run is None:
            self._backup_profiles.mark_run(profile.id, now)  # point de départ du compteur
            return False
        return (now - profile.last_run).total_seconds() >= profile.hours * 3600

    def list_backups(self, user: User) -> list[BackupArchive]:
        """Archives existantes (même barrière que le déclenchement — lecture,
        non auditée en succès)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        return self._backup_archives.list_archives()

    def backup_progress(self, user: User) -> BackupProgress | None:
        """Sauvegarde manuelle/automatique en cours, si elle existe.

        mc-backup ne publie pas de progression native. Comme pour la sauvegarde
        de sécurité avant restauration, on estime depuis la taille de l'archive
        en cours comparée à la précédente du même type.

        C'est aussi ici (appelé par le polling du bandeau) qu'un cycle TERMINÉ
        est transformé en résultat persistant (`BackupOutcome`) : archive
        produite = succès, aucune archive = échec.
        """
        self._authorize(user, Permission.BACKUP_TRIGGER)
        running = None
        if self._backup_profiles is not None and self._profile_backup is not None:
            for profile in self._backup_profiles.list_profiles():
                if profile.id not in self._backup_started_at:
                    continue
                progress = self._backup_progress_for(profile.id, f"{profile.dest}/", self._profile_backup)
                if progress is not None:
                    running = BackupProgress(
                        kind=progress.kind, running=progress.running,
                        progress_percent=progress.progress_percent,
                        archive=progress.archive, label=profile.name,
                    )
                    break
        if running is None:
            running = self._backup_progress_for("safety", "restore-safety/", self._restore_safety_backup)
        self._scan_backup_outcomes()
        return running

    def _scan_backup_outcomes(self) -> None:
        """Constate les cycles de sauvegarde TERMINÉS (BackupOutcome pour le
        bandeau + entrée d'audit) — appelé par le polling de la page ET par le
        coordinateur de fond, pour qu'une sauvegarde nocturne laisse sa trace
        au journal même si personne ne regarde."""
        watched: list[tuple[str, str, object, str]] = [
            ("safety", "restore-safety/", self._restore_safety_backup, "")]
        if self._backup_profiles is not None and self._profile_backup is not None:
            for profile in self._backup_profiles.list_profiles():
                watched.append((profile.id, f"{profile.dest}/", self._profile_backup, profile.name))
        for kind, prefix, port, label in watched:
            if kind not in self._backup_started_at:
                continue
            try:
                if port.is_running():
                    continue
            except Exception:  # noqa: BLE001 — état inconnu : on retentera au prochain passage
                continue
            if kind == "safety":
                # Cycle de sécurité : son issue est déjà racontée par les
                # événements de la restauration, pas de doublon.
                self._backup_started_at.pop("safety", None)
                self._backup_operation_ids.pop("safety", None)
                self._backup_progress_max.pop("safety", None)
                continue
            self._finalize_backup(kind, prefix, label)

    def _finalize_backup(self, kind: str, prefix: str, label: str = "") -> None:
        started_at = self._backup_started_at.pop(kind, None)
        if started_at is None:
            return
        self._backup_progress_max.pop(kind, None)
        produced = [
            a for a in self._backup_archives.list_archives()
            if a.filename.startswith(prefix) and a.created_at >= started_at
        ]
        newest = max(produced, key=lambda a: a.created_at, default=None)
        operation_id = self._backup_operation_ids.pop(kind, "")
        self._backup_result = BackupOutcome(
            kind=kind, ok=newest is not None, finished_at=self._clock.now(),
            archive=newest.filename if newest else None, label=label,
        )
        # L'ISSUE laisse sa trace au journal (avant : seul le déclenchement
        # était audité — une sauvegarde nocturne ratée était invisible).
        if newest is not None:
            self._record(_BACKUP_WATCH_USER, Permission.BACKUP_TRIGGER, "allowed",
                         f"phase=backup_done kind={kind} archive={newest.filename} "
                         f"size_mio={newest.size_bytes // 1048576} operation_id={operation_id}")
            self._notify.notify(
                "Sauvegarde terminée",
                f"« {label or kind} » — {newest.size_bytes // 1048576} Mio.",
                "info", event="backup")
            self._auto_retention(kind, operation_id)
        else:
            self._record(_BACKUP_WATCH_USER, Permission.BACKUP_TRIGGER, "error",
                         f"phase=backup_failed kind={kind} operation_id={operation_id}")
            self._notify.notify("Sauvegarde échouée",
                                f"La sauvegarde {kind} n'a produit aucune archive.", "error")

    def _auto_retention(self, kind: str, operation_id: str) -> None:
        """Applique la politique de rétention du profil après une sauvegarde
        RÉUSSIE (retour Jeremy : une politique s'applique toute seule, le
        bouton manuel était contre-intuitif). Physiquement borné : la
        suppression n'est possible que sous scheduled/ et profiles/."""
        if self._backup_profiles is None:
            return
        profile = self._backup_profiles.get(kind)
        if profile is None or profile.retention_daily_days <= 0:
            return
        if not (profile.dest.startswith("profiles/") or profile.dest == "scheduled"):
            return
        try:
            plan = self.backup_retention_plan(
                _BACKUP_WATCH_USER, daily_days=profile.retention_daily_days,
                monthly_months=profile.retention_monthly_months, prefix=f"{profile.dest}/")
            deleted = 0
            for item in plan.delete:
                if self._backup_archives.delete_archive(item.archive.filename):
                    deleted += 1
            if deleted:
                self._record(_BACKUP_WATCH_USER, Permission.BACKUP_TRIGGER, "allowed",
                             f"phase=retention_applied kind={kind} deleted={deleted} "
                             f"operation_id={operation_id}")
        except Exception:  # noqa: BLE001 — la rétention ne doit jamais faire échouer l'issue
            pass

    def last_backup_outcome(self, user: User) -> BackupOutcome | None:
        """Dernier résultat de sauvegarde non fermé (lecture non auditée)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        return self._backup_result

    def archive_checks(self, user: User) -> dict[str, ArchiveCheck]:
        """Verdicts d'intégrité des archives (lecture non auditée)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        if self._archive_checks is None:
            return {}
        return self._archive_checks.statuses()

    def archive_checking(self, user: User) -> str | None:
        """Archive en cours de vérification d'intégrité (lecture non auditée)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        if self._archive_checks is None:
            return None
        return self._archive_checks.checking()

    def backup_free_bytes(self, user: User) -> int | None:
        """Espace libre du volume des sauvegardes (lecture non auditée)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        return self._backup_archives.free_bytes()

    def storage_overview(self, user: User, days: int = 30) -> tuple[list[StorageSnapshot], date | None]:
        """Historique de stockage + projection de saturation (backlog
        fiabilité n° 6). Même barrière que le reste de la page (lecture non
        auditée)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        if self._storage_history is None:
            return [], None
        history = self._storage_history.history(days)
        return history, _project_saturation(history)

    def backup_download_path(self, user: User, filename: str):
        """Chemin local d'une archive téléchargeable."""
        self._authorize(user, Permission.BACKUP_DOWNLOAD)
        path = self._backup_archives.archive_path(filename)
        if path is None:
            raise BackupArchiveNotFound(f"archive introuvable : {filename}")
        return path

    def restore_preflight(self, user: User, filename: str) -> tuple[str, ...]:
        """Vérifications non mutantes avant restauration. Ne purge PAS un
        résultat encore affiché : ouvrir la modale ne doit pas faire
        disparaître le bandeau (seule la croix ou une nouvelle restauration
        l'efface)."""
        self._authorize(user, Permission.BACKUP_RESTORE)
        return tuple(self._restore_preflight_issues(filename))

    def _restore_preflight_issues(self, filename: str, *, check_archive: bool = True) -> list[str]:
        issues: list[str] = []
        if self._restore_operation is not None and self._restore_operation.phase not in ("success", "failed"):
            issues.append("une restauration est déjà en cours")
        if self._pending_restore is None:
            issues.append("coordinateur de restauration non configuré")
        elif self._pending_restore.get() is not None:
            issues.append("une restauration attend déjà sa sauvegarde de sécurité")
        if self._restore is None:
            issues.append("conteneur de restauration non configuré")
        elif self._port_running(self._restore, "restauration"):
            issues.append("le conteneur de restauration est déjà en cours")
        if self._archive_validator is None:
            issues.append("validation immédiate des archives non configurée")
        if check_archive:
            issues.extend(self._restore_archive_issues(filename))

        watched: list[tuple[str, object]] = [("sauvegarde de sécurité", self._restore_safety_backup)]
        if self._profile_backup is not None:
            watched.append(("sauvegarde de profil", self._profile_backup))
        for label, backup in watched:
            if self._port_running(backup, label):
                issues.append(f"{label} déjà en cours")

        # Fail-closed (include_unknown=True) : la restauration est la seule
        # opération destructive — un outil périmé OU invérifiable la bloque.
        if self._worker_integrity is not None:
            issues.extend(
                self._worker_integrity.issues("restore", include_unknown=True)
            )
        return issues

    def _restore_in_progress(self) -> bool:
        if self._pending_restore is not None and self._pending_restore.get() is not None:
            return True
        if (
            self._restore_operation is not None
            and self._restore_operation.phase not in ("success", "failed")
        ):
            return True
        if self._restore is None:
            return False
        try:
            return bool(self._restore.is_running())
        except Exception:  # noqa: BLE001 - une sauvegarde ne part pas sur un état inconnu
            return True

    def _restore_archive_issues(self, filename: str) -> list[str]:
        """Vérifie qu'un verdict complet correspond au fichier actuel."""
        try:
            archive = next(
                (
                    item
                    for item in self._backup_archives.list_archives()
                    if item.filename == filename
                ),
                None,
            )
            path = self._backup_archives.archive_path(filename)
        except Exception as exc:  # noqa: BLE001
            return [f"accès aux sauvegardes impossible : {exc}"]
        if archive is None or path is None:
            return [f"archive introuvable : {filename}"]
        if self._archive_checks is None:
            return ["vérification d'intégrité des archives non configurée"]
        try:
            check = self._archive_checks.statuses().get(filename)
            checking = self._archive_checks.checking()
        except Exception as exc:  # noqa: BLE001
            return [f"verdict d'intégrité illisible : {exc}"]
        if check is None:
            if checking == filename:
                return ["vérification complète de l'archive en cours"]
            return ["archive pas encore vérifiée ; attendre le badge « vérifiée »"]
        if (
            check.size_bytes != archive.size_bytes
            or check.archive_created_at != archive.created_at
        ):
            if checking == filename:
                return ["archive modifiée ; nouvelle vérification en cours"]
            return [
                "archive modifiée depuis son dernier contrôle ; "
                "nouvelle vérification requise"
            ]
        if not check.ok:
            detail = check.detail.strip() or "erreur de lecture"
            return [f"archive illisible ou dangereuse : {detail}"]
        if check.restorable is None:
            return ["ancien verdict incomplet ; nouvelle vérification requise"]
        if not check.restorable:
            detail = check.detail.strip() or "monde Minecraft complet absent"
            return [f"archive non restaurable : {detail}"]
        return []

    def _port_running(self, port, label: str) -> bool:
        try:
            return bool(port.is_running())
        except Exception as exc:  # noqa: BLE001
            raise RestoreUnavailable(f"état {label} illisible : {exc}") from exc

    # Temps maximal accordé à la sauvegarde de sécurité avant d'abandonner la
    # restauration (constaté ~90 s en réel : très large marge).
    _RESTORE_WAIT_MAX = timedelta(minutes=15)
    _RESTORE_EXECUTION_MAX = timedelta(minutes=30)
    _RESTORE_HEALTH_MAX = timedelta(minutes=5)

    def _backup_progress_for(self, kind: str, prefix: str, backup: BackupPort) -> BackupProgress | None:
        try:
            running = backup.is_running()
        except Exception:  # noqa: BLE001 — suivi non critique : l'UI réessaiera au prochain poll
            return None
        if not running:
            return None

        started_at = self._backup_started_at.get(kind)
        archives = [a for a in self._backup_archives.list_archives() if a.filename.startswith(prefix)]
        archives.sort(key=lambda a: a.created_at, reverse=True)
        current = next((a for a in archives if started_at is not None and a.created_at >= started_at), None)
        baseline = next((a for a in archives if started_at is None or a.created_at < started_at), None)
        progress = None
        if current is not None and baseline is not None and baseline.size_bytes > 0:
            progress = max(1, min(95, int(current.size_bytes / baseline.size_bytes * 100)))
            progress = max(progress, self._backup_progress_max.get(kind, 0))
            self._backup_progress_max[kind] = progress
        return BackupProgress(kind, True, progress, current)

    def restore_backup(self, user: User, filename: str) -> str:
        """Restaure une archive — ÉCRASE le monde en cours (owner, V5.7).

        Ordre : (1) valider l'archive ; (2) déclencher la sauvegarde de sécurité
        du monde ACTUEL ; (3) attendre sa fin via `tick_pending_restore` avant
        de démarrer mc-restore. Si la sauvegarde de sécurité ne peut pas
        démarrer, la restauration est annulée : une opération destructive ne
        continue jamais sans filet de sécurité.

        Retourne "pending" : la restauration attend sa sauvegarde de sécurité."""
        self._authorize(user, Permission.BACKUP_RESTORE)
        with self._backup_restore_lock:
            return self._restore_backup_locked(user, filename)

    def _restore_backup_locked(self, user: User, filename: str) -> str:
        if self._restore_operation is not None and self._restore_operation.phase in ("success", "failed"):
            self._restore_operation = None
        if self._backup_archives.archive_path(filename) is None:
            self._restore_event(user, "restore_rejected", "error", archive=filename, reason="archive_not_found")
            raise BackupArchiveNotFound(f"archive introuvable : {filename}")
        issues = self._restore_preflight_issues(filename)
        if issues:
            detail = "; ".join(issues)
            self._restore_event(user, "restore_rejected", "error", archive=filename, reason="preflight_failed", detail=detail)
            raise RestoreUnavailable(f"préflight échoué : {detail}")
        operation_id = self._new_restore_operation_id(filename)
        self._restore_event(user, "restore_requested", archive=filename, requested_by=user.username, operation_id=operation_id)
        safety_archives_before = tuple(
            archive.filename
            for archive in self._backup_archives.list_archives()
            if archive.filename.startswith("restore-safety/")
        )

        try:
            result = self._restore_safety_backup.trigger()
            if not result.started:
                raise BackupUnavailable(result.detail or "le conteneur n'a pas démarré")
            self._backup_started_at["safety"] = self._clock.now()
            self._backup_progress_max.pop("safety", None)
            self._restore_event(user, "security_backup_started", archive=filename, operation_id=operation_id, detail=result.detail)
        except Exception as exc:  # noqa: BLE001
            self._restore_event(user, "security_backup_failed", "error", archive=filename, operation_id=operation_id, error=exc)
            self._notify_restore(
                "Restauration annulée",
                f"{filename} : la sauvegarde de sécurité n'a pas pu démarrer. "
                "Le monde n'a pas été modifié.",
                "error",
            )
            raise RestoreUnavailable(
                f"sauvegarde de sécurité impossible : {exc}; restauration annulée"
            ) from exc

        deadline = self._clock.now() + self._RESTORE_WAIT_MAX
        self._pending_restore.set(
            PendingRestore(
                filename=filename,
                requested_by=user.username,
                deadline=deadline,
                operation_id=operation_id,
                safety_archives_before=safety_archives_before,
            )
        )
        self._restore_event(
            user,
            "restore_pending",
            archive=filename,
            requested_by=user.username,
            operation_id=operation_id,
            deadline=deadline.isoformat(),
        )
        self._notify_restore(
            "Restauration programmée",
            f"{filename} — démarrera dès la fin de la sauvegarde de sécurité.",
            "info",
        )
        return "pending"

    def pending_restore_status(self, user: User) -> PendingRestore | None:
        """Restauration en attente, s'il y en a une (lecture non auditée)."""
        self._authorize(user, Permission.BACKUP_RESTORE)
        if self._pending_restore is None:
            return None
        return self._pending_restore.get()

    def dismiss_activity_result(self, user: User) -> bool:
        """Efface le(s) résultat(s) affiché(s) par le bandeau (sauvegarde et,
        si l'utilisateur y a droit, restauration) — action d'affichage pure,
        volontairement non auditée (le journal contient déjà les événements)."""
        self._authorize(user, Permission.BACKUP_TRIGGER)
        cleared = self._backup_result is not None
        self._backup_result = None
        if user.can(Permission.BACKUP_RESTORE):
            op = self._restore_operation
            if op is not None and op.phase in ("success", "failed"):
                self._restore_operation = None
                cleared = True
        return cleared

    def restore_progress(self, user: User) -> RestoreProgress:
        """Suivi best-effort d'une restauration en attente (lecture non auditée).

        mc-backup ne publie pas de pourcentage natif. Pendant la sauvegarde de
        sécurité, on estime donc la progression depuis la taille de l'archive
        en cours, comparée à la dernière sauvegarde manuelle complète.
        """
        self._authorize(user, Permission.BACKUP_RESTORE)
        pending = self._pending_restore.get() if self._pending_restore is not None else None
        if pending is None:
            op = self._restore_operation
            if op is None:
                return RestoreProgress(None, "idle", False, None)
            level = "success" if op.phase == "success" else "error" if op.phase == "failed" else "info"
            return RestoreProgress(
                None,
                op.phase,
                False,
                100 if op.phase in ("success", "failed", "waiting_minecraft") else None,
                operation_id=self._restore_operation_id(op),
                filename=op.filename,
                message=op.message,
                level=level,
            )

        requested_at = pending.deadline - self._RESTORE_WAIT_MAX
        archives = self._backup_archives.list_archives()
        safety_after = [
            a for a in archives
            if a.filename.startswith("restore-safety/") and a.created_at >= requested_at
        ]
        safety_before = [
            a for a in archives
            if a.filename.startswith("restore-safety/") and a.created_at < requested_at
        ]
        safety_after.sort(key=lambda a: a.created_at, reverse=True)
        safety_before.sort(key=lambda a: a.created_at, reverse=True)
        security_archive = safety_after[0] if safety_after else None
        baseline_size = safety_before[0].size_bytes if safety_before else 0

        backup_running = False
        try:
            backup_running = self._restore_safety_backup.is_running()
        except Exception:  # noqa: BLE001 — état inconnu : suivi indéterminé jusqu'au prochain poll
            backup_running = False

        if self._safety_validation_filename is not None:
            validating_archive = next(
                (
                    archive
                    for archive in safety_after
                    if archive.filename == self._safety_validation_filename
                ),
                security_archive,
            )
            return RestoreProgress(
                pending,
                "backup_validating",
                False,
                None,
                validating_archive,
                operation_id=pending.operation_id,
                filename=pending.filename,
                message="Étape 1/3 — vérification complète de la sauvegarde de sécurité.",
            )
        if backup_running:
            progress = None
            if security_archive is not None and baseline_size > 0:
                progress = max(1, min(95, int(security_archive.size_bytes / baseline_size * 100)))
                progress = max(progress, self._backup_progress_max.get("safety", 0))
                self._backup_progress_max["safety"] = progress
            return RestoreProgress(
                pending,
                "backup_running",
                True,
                progress,
                security_archive,
                operation_id=pending.operation_id,
                filename=pending.filename,
                message="Étape 1/3 — sauvegarde de sécurité du monde actuel.",
            )
        return RestoreProgress(
            pending,
            "restore_pending",
            False,
            100,
            security_archive,
            operation_id=pending.operation_id,
            filename=pending.filename,
            message="Étape 1/3 terminée — lancement de la restauration…",
        )

    def _new_restore_operation_id(self, filename: str) -> str:
        return f"{self._clock.now().isoformat()}::{filename}"

    def _restore_operation_id(self, op: RestoreOperation) -> str:
        return op.operation_id or f"{op.started_at.isoformat()}::{op.filename}"

    def _finish_restore_operation(self, phase: str, message: str) -> str:
        op = self._restore_operation
        if op is None:
            return phase
        self._restore_operation = RestoreOperation(
            filename=op.filename,
            requested_by=op.requested_by,
            phase=phase,
            started_at=op.started_at,
            deadline=op.deadline,
            message=message,
            completed_at=self._clock.now(),
            operation_id=op.operation_id,
        )
        return phase

    def _start_restore_after_backup(self, user: User, filename: str, requested_by: str, operation_id: str = "") -> None:
        self._restore.restore(filename)
        now = self._clock.now()
        if not operation_id:
            operation_id = self._new_restore_operation_id(filename)
        self._restore_operation = RestoreOperation(
            filename=filename,
            requested_by=requested_by,
            phase="restore_running",
            started_at=now,
            deadline=now + self._RESTORE_EXECUTION_MAX,
            message="Étape 2/3 — remplacement du monde par l'archive.",
            operation_id=operation_id,
        )
        self._restore_event(user, "restore_started", archive=filename, requested_by=requested_by, operation_id=operation_id)

    def _tick_restore_operation(self, user: User) -> str | None:
        op = self._restore_operation
        if op is None:
            return None
        now = self._clock.now()
        if op.phase in ("success", "failed"):
            # Résultat conservé jusqu'à fermeture explicite (croix du bandeau,
            # dismiss_restore_result) ou nouvelle restauration — un succès qui
            # s'efface tout seul au bout de 45 s était raté trop souvent.
            return op.phase
        if now >= op.deadline:
            if op.phase == "restore_running":
                self._restore_event(
                    user,
                    "restore_failed",
                    "error",
                    archive=op.filename,
                    requested_by=op.requested_by,
                    operation_id=self._restore_operation_id(op),
                    reason="execution_timeout",
                )
                return self._finish_restore_operation(
                    "failed",
                    "Le conteneur de restauration n'a pas terminé dans les temps.",
                )
            self._restore_event(
                user,
                "minecraft_unhealthy_timeout",
                "error",
                archive=op.filename,
                requested_by=op.requested_by,
                operation_id=self._restore_operation_id(op),
            )
            self._restore_event(user, "restore_failed", "error", archive=op.filename, operation_id=self._restore_operation_id(op), reason="health_timeout")
            return self._finish_restore_operation(
                "failed",
                "Le monde a été restauré, mais le serveur n'est pas revenu en ligne dans les temps.",
            )

        if op.phase == "restore_running":
            try:
                if self._restore is not None and self._restore.is_running():
                    return None
            except Exception as exc:  # noqa: BLE001
                self._restore_event(user, "restore_failed", "error", archive=op.filename, operation_id=self._restore_operation_id(op), error=exc)
                return self._finish_restore_operation("failed", "État de mc-restore illisible.")
            try:
                exit_code = self._restore.exit_code() if self._restore is not None else None
            except Exception as exc:  # noqa: BLE001
                self._restore_event(user, "restore_failed", "error", archive=op.filename, operation_id=self._restore_operation_id(op), error=exc)
                self._notify_restore("Restauration échouée", str(exc), "error")
                return self._finish_restore_operation(
                    "failed", "Le résultat du conteneur de restauration est illisible."
                )
            if exit_code != 0:
                detail = (
                    f"code de sortie {exit_code}"
                    if exit_code is not None
                    else "code de sortie inconnu"
                )
                self._restore_event(
                    user,
                    "restore_failed",
                    "error",
                    archive=op.filename,
                    operation_id=self._restore_operation_id(op),
                    reason="container_exit",
                    detail=detail,
                )
                message = f"Le conteneur de restauration a échoué ({detail})."
                self._notify_restore("Restauration échouée", message, "error")
                return self._finish_restore_operation("failed", message)
            self._restore_event(user, "restore_done", archive=op.filename, operation_id=self._restore_operation_id(op))
            self._restore_event(user, "minecraft_restarting", archive=op.filename, operation_id=self._restore_operation_id(op))
            self._restore_operation = RestoreOperation(
                filename=op.filename,
                requested_by=op.requested_by,
                phase="waiting_minecraft",
                started_at=op.started_at,
                deadline=now + self._RESTORE_HEALTH_MAX,
                message="Étape 3/3 — redémarrage du serveur.",
                operation_id=op.operation_id,
            )
            op = self._restore_operation

        if op.phase == "waiting_minecraft":
            try:
                state = self._container.status()
            except Exception:  # noqa: BLE001
                return None
            if not state.running:
                return None
            if state.health is not None and state.health != "healthy":
                return None
            try:
                self._game.list_players()
            except Exception:  # noqa: BLE001
                return None
            self._restore_event(user, "minecraft_healthy", archive=op.filename, operation_id=self._restore_operation_id(op), health=state.health or "rcon_ok")
            self._restore_event(user, "restore_completed", archive=op.filename, requested_by=op.requested_by, operation_id=self._restore_operation_id(op))
            return self._finish_restore_operation("success", "Le monde a été restauré, le serveur est de nouveau en ligne.")
        return None

    def _remember_pending_restore_failure(
        self,
        pending: PendingRestore,
        message: str,
    ) -> None:
        """Conserve l'échec dans le bandeau même si mc-restore n'a pas démarré."""
        now = self._clock.now()
        self._restore_operation = RestoreOperation(
            filename=pending.filename,
            requested_by=pending.requested_by,
            phase="failed",
            started_at=pending.deadline - self._RESTORE_WAIT_MAX,
            deadline=now,
            message=message,
            completed_at=now,
            operation_id=pending.operation_id,
        )

    def _new_safety_archive(self, pending: PendingRestore) -> BackupArchive | None:
        before = set(pending.safety_archives_before)
        candidates = [
            archive
            for archive in self._backup_archives.list_archives()
            if archive.filename.startswith("restore-safety/")
            and archive.filename not in before
        ]
        return max(candidates, key=lambda archive: archive.created_at, default=None)

    def _validate_new_safety_archive(self, pending: PendingRestore) -> BackupArchive:
        archive = self._new_safety_archive(pending)
        if archive is None:
            raise RestoreUnavailable(
                "le conteneur s'est terminé sans produire de nouvelle archive de sécurité"
            )
        if self._archive_validator is None:
            raise RestoreUnavailable("validation immédiate des archives non configurée")

        self._safety_validation_filename = archive.filename
        try:
            check = self._archive_validator.validate(archive.filename)
        finally:
            self._safety_validation_filename = None

        current = next(
            (
                item
                for item in self._backup_archives.list_archives()
                if item.filename == archive.filename
            ),
            None,
        )
        if current is None:
            raise RestoreUnavailable(
                "la sauvegarde de sécurité a disparu pendant sa vérification"
            )
        if (
            check.filename != current.filename
            or check.size_bytes != current.size_bytes
            or check.archive_created_at != current.created_at
        ):
            raise RestoreUnavailable(
                "la sauvegarde de sécurité a changé pendant sa vérification"
            )
        if not check.ok:
            detail = check.detail.strip() or "erreur de lecture"
            raise RestoreUnavailable(f"sauvegarde de sécurité illisible : {detail}")
        if not check.restorable:
            detail = check.detail.strip() or "monde Minecraft complet absent"
            raise RestoreUnavailable(f"sauvegarde de sécurité non restaurable : {detail}")
        return current

    def tick_pending_restore(self, user: User) -> str | None:
        """Démarre la restauration en attente une fois la sauvegarde de
        sécurité terminée (appelé en boucle par RestoreCoordinator).

        Passé la deadline (sauvegarde anormalement longue ou état du conteneur
        illisible), ABANDONNE : le monde n'a pas été touché — ne jamais
        restaurer sur la foi d'un état inconnu."""
        self._authorize(user, Permission.BACKUP_RESTORE)
        self._scan_backup_outcomes()
        if self._pending_restore is None:
            return self._tick_restore_operation(user)
        pending = self._pending_restore.get()
        if pending is None:
            return self._tick_restore_operation(user)
        if self._clock.now() >= pending.deadline:
            self._pending_restore.clear()
            message = (
                f"{pending.filename} : la sauvegarde de sécurité n'a pas terminé "
                "dans les temps. Le monde n'a pas été modifié."
            )
            self._restore_event(
                user,
                "security_backup_timeout",
                "error",
                archive=pending.filename,
                requested_by=pending.requested_by,
                operation_id=pending.operation_id,
            )
            self._remember_pending_restore_failure(pending, message)
            self._notify_restore(
                "Restauration abandonnée",
                message,
                "error",
            )
            return "abandoned"
        try:
            if self._restore_safety_backup.is_running():
                return None  # sauvegarde encore en cours : on repassera au prochain poll
        except Exception:  # noqa: BLE001 — état inconnu (proxy ?) : attendre, la deadline tranchera
            return None
        try:
            exit_code = self._restore_safety_backup.exit_code()
        except Exception:  # noqa: BLE001 — résultat inconnu : attendre, la deadline tranchera
            return None
        if exit_code != 0:
            self._pending_restore.clear()
            detail = (
                f"code de sortie {exit_code}"
                if exit_code is not None
                else "code de sortie inconnu"
            )
            message = (
                f"{pending.filename} : la sauvegarde de sécurité a échoué ({detail}). "
                "Le monde n'a pas été modifié."
            )
            self._restore_event(
                user,
                "security_backup_failed",
                "error",
                archive=pending.filename,
                requested_by=pending.requested_by,
                operation_id=pending.operation_id,
                reason="container_exit",
                detail=detail,
            )
            self._remember_pending_restore_failure(pending, message)
            self._notify_restore(
                "Restauration annulée",
                message,
                "error",
            )
            return "abandoned"

        try:
            safety_archive = self._validate_new_safety_archive(pending)
        except Exception as exc:  # noqa: BLE001
            self._pending_restore.clear()
            message = (
                f"{pending.filename} : {exc}. "
                "La restauration est annulée et le monde n'a pas été modifié."
            )
            self._restore_event(
                user,
                "security_backup_failed",
                "error",
                archive=pending.filename,
                requested_by=pending.requested_by,
                operation_id=pending.operation_id,
                reason="archive_validation",
                detail=str(exc),
            )
            self._remember_pending_restore_failure(pending, message)
            self._notify_restore("Restauration annulée", message, "error")
            return "abandoned"

        target_issues = self._restore_archive_issues(pending.filename)
        if target_issues:
            self._pending_restore.clear()
            detail = "; ".join(target_issues)
            message = (
                f"{pending.filename} n'est plus utilisable : {detail}. "
                "La restauration est annulée et le monde n'a pas été modifié."
            )
            self._restore_event(
                user,
                "restore_failed",
                "error",
                archive=pending.filename,
                requested_by=pending.requested_by,
                operation_id=pending.operation_id,
                reason="target_revalidation",
                detail=detail,
            )
            self._remember_pending_restore_failure(pending, message)
            self._notify_restore("Restauration annulée", message, "error")
            return "abandoned"

        try:
            self._restore_event(
                user,
                "security_backup_done",
                archive=pending.filename,
                security_archive=safety_archive.filename,
                requested_by=pending.requested_by,
                operation_id=pending.operation_id,
            )
            self._start_restore_after_backup(user, pending.filename, requested_by=pending.requested_by, operation_id=pending.operation_id)
            self._pending_restore.clear()
        except Exception as exc:  # noqa: BLE001
            self._pending_restore.clear()
            self._restore_event(user, "restore_failed", "error", archive=pending.filename, operation_id=pending.operation_id, error=exc)
            self._notify_restore("Restauration échouée", str(exc), "error")
            raise
        self._notify_restore("Restauration lancée", f"{pending.filename} — le serveur va redémarrer.", "info")
        return "restored"

    def _notify_restore(self, title: str, message: str, level: str = "info") -> None:
        # L'interrupteur « restore » (notifications.json, réglé dans l'UI)
        # filtre dans le notifier — plus de flag d'env.
        self._notify.notify(title, message, level, event="restore")

    def backup_retention_plan(
        self,
        user: User,
        daily_days: int = 30,
        monthly_months: int = 12,
        prefix: str = "scheduled/",
    ) -> BackupRetentionPlan:
        """Dry-run de rétention.

        Politique :
          - garder toutes les archives des N derniers jours ;
          - ensuite, garder la plus récente de chaque mois pendant M mois ;
          - supprimer le reste.
        """
        self._authorize(user, Permission.BACKUP_RETENTION)
        daily_days = max(1, int(daily_days))
        monthly_months = max(0, int(monthly_months))
        monthly_until_days = max(daily_days, monthly_months * 31)
        prefix = prefix if prefix.endswith("/") else f"{prefix}/"
        now = self._clock.now()
        archives = sorted(
            self._backup_archives.list_archives(),
            key=lambda a: a.created_at,
            reverse=True,
        )
        monthly_kept: set[tuple[int, int]] = set()
        keep: list[BackupRetentionItem] = []
        delete: list[BackupRetentionItem] = []
        for archive in archives:
            if not archive.filename.startswith(prefix):
                continue  # hors périmètre : ni comptée ni supprimable
            age = now - archive.created_at
            age_days = age.total_seconds() / 86400
            month_key = (archive.created_at.year, archive.created_at.month)
            if age_days <= daily_days:
                monthly_kept.add(month_key)
                keep.append(BackupRetentionItem(archive, f"journalier (< {daily_days} jours)"))
                continue
            if age_days <= monthly_until_days:
                if month_key not in monthly_kept:
                    monthly_kept.add(month_key)
                    keep.append(BackupRetentionItem(archive, f"mensuel conservé ({monthly_months} mois)"))
                else:
                    delete.append(BackupRetentionItem(archive, "doublon mensuel"))
                continue
            delete.append(BackupRetentionItem(archive, f"> {monthly_months} mois"))
        return BackupRetentionPlan(keep=tuple(keep), delete=tuple(delete))

    def apply_backup_retention(
        self,
        user: User,
        daily_days: int = 30,
        monthly_months: int = 12,
        prefix: str = "scheduled/",
    ) -> int:
        """Applique la politique de rétention : supprime RÉELLEMENT les
        archives automatiques obsolètes (le plan est recalculé à l'instant de
        l'application — jamais de liste de fichiers venue du client). Les
        manuelles et l'historique ne sont jamais candidates (et le montage ne
        permet d'écrire QUE dans scheduled/). Renvoie le nombre supprimé."""
        plan = self.backup_retention_plan(user, daily_days=daily_days, monthly_months=monthly_months, prefix=prefix)
        deleted, freed, failed = 0, 0, []
        for item in plan.delete:
            if self._backup_archives.delete_archive(item.archive.filename):
                deleted += 1
                freed += item.archive.size_bytes
            else:
                failed.append(item.archive.filename)
        detail = (f"rétention appliquée ({daily_days} j / {monthly_months} mois) : "
                  f"{deleted} supprimée(s), {freed // 1048576} Mio libérés")
        if failed:
            detail += f" — échecs : {', '.join(failed)}"
        self._record(user, Permission.BACKUP_RETENTION, "allowed" if not failed else "error", detail)
        return deleted

