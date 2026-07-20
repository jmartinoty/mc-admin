"""Fakes des ports — le domaine se teste SANS Docker ni RCON réels.

Chaque fake implémente structurellement un Protocol de domain.ports et
enregistre ses appels, pour vérifier les effets (ex. : le port n'est PAS appelé
quand la RBAC refuse).
"""
from __future__ import annotations

from datetime import datetime, timezone

from domain.errors import (
    MaintenanceUnavailable,
    RestoreUnavailable,
    ServerUnavailable,
    UpdateUnavailable,
)
from domain.model import (
    ArchiveCheck,
    AuditEntry,
    BackupArchive,
    BackupResult,
    BanEntry,
    ContainerState,
    MetricReading,
    OpEntry,
    PendingOpLevel,
    PendingUnban,
    Player,
    PlayerSession,
    PlayerStats,
    PlayerSummary,
    UpdateStatus,
)


class FakeGame:
    def __init__(self, players=None, available=True, whitelist=None, ops=None):
        self._players = list(players or [])
        self._available = available
        self.whitelist_names = list(whitelist or [])
        self.whitelist_calls: list[tuple[str, str]] = []
        self.op_names = list(ops or [])
        self.op_calls: list[tuple[str, str]] = []
        self.kick_calls: list[tuple[str, str]] = []
        self.ban_calls: list[tuple[str, str, str]] = []
        self.raw_calls: list[str] = []
        self.saved = 0
        self.said: list[str] = []
        self.stopped = 0
        self.difficulty = "normal"
        self.gamerules = {"keep_inventory": False, "mob_griefing": True, "advance_time": True}
        self.game_calls: list[tuple] = []

    def list_players(self) -> list[Player]:
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        return list(self._players)

    def save_all(self) -> None:
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        self.saved += 1

    def say(self, message: str) -> None:
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        self.said.append(message)

    def raw(self, command: str) -> str:
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        self.raw_calls.append(command)
        return f"ran: {command}"

    def stop(self) -> None:
        self.stopped += 1

    def whitelist_list(self) -> list[str]:
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        return list(self.whitelist_names)

    def whitelist_add(self, player_name: str) -> str:
        self.whitelist_calls.append(("add", player_name))
        self.whitelist_names.append(player_name)
        return f"Added {player_name} to the whitelist"

    def whitelist_remove(self, player_name: str) -> str:
        self.whitelist_calls.append(("remove", player_name))
        if player_name in self.whitelist_names:
            self.whitelist_names.remove(player_name)
        return f"Removed {player_name} from the whitelist"

    def op(self, player_name: str) -> str:
        self.op_calls.append(("op", player_name))
        if player_name not in self.op_names:
            self.op_names.append(player_name)
        return f"Made {player_name} a server operator"

    def deop(self, player_name: str) -> str:
        self.op_calls.append(("deop", player_name))
        if player_name in self.op_names:
            self.op_names.remove(player_name)
        return f"Made {player_name} no longer a server operator"

    def kick(self, player_name: str, reason: str = "") -> str:
        self.kick_calls.append((player_name, reason))
        return f"Kicked {player_name}"

    def ban(self, player_name: str, reason: str = "") -> str:
        self.ban_calls.append(("ban", player_name, reason))
        return f"Banned {player_name}"

    def pardon(self, player_name: str) -> str:
        self.ban_calls.append(("pardon", player_name, ""))
        return f"Unbanned {player_name}"

    # Réglages de jeu (V5)
    def get_difficulty(self):
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        return self.difficulty

    def set_difficulty(self, level: str) -> str:
        self.game_calls.append(("difficulty", level))
        self.difficulty = level
        return f"The difficulty has been set to {level}"

    def get_gamerule(self, name: str):
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        return self.gamerules.get(name)

    def set_gamerule(self, name: str, value) -> str:
        self.game_calls.append(("gamerule", name, value))
        self.gamerules[name] = value
        return f"Gamerule {name} is now set to: {str(value).lower()}"

    def list_gamerule_names(self) -> list[str]:
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        return sorted(set(self.gamerules) | {"random_tick_speed"})

    def get_gamerules(self, names):
        if not self._available:
            raise ServerUnavailable("rcon injoignable")
        defaults = {"random_tick_speed": "3"}
        out = {}
        for n in names:
            v = self.gamerules.get(n, defaults.get(n))
            out[n] = str(v).lower() if isinstance(v, bool) else str(v)
        return out

    def set_weather(self, kind: str) -> str:
        self.game_calls.append(("weather", kind))
        return f"Set the weather to {kind}"

    def set_time(self, moment: str) -> str:
        self.game_calls.append(("time", moment))
        return f"Set the time to {moment}"


class FakeContainer:
    def __init__(self, running=True, fail=False, name="minecraft", health=None, started_at=None):
        self._running = running
        self._fail = fail
        self._name = name
        self.health = health
        self.started_at = started_at
        self.restarts = 0
        self.starts = 0
        self.stops = 0
        self.stop_timeouts = []

    def status(self) -> ContainerState:
        return ContainerState(
            name=self._name,
            running=self._running,
            status="running" if self._running else "exited",
            health=self.health,
            started_at=self.started_at,
        )

    def restart(self) -> None:
        if self._fail:
            raise RuntimeError("proxy 500")
        self.restarts += 1

    def start(self) -> None:
        if self._fail:
            raise RuntimeError("proxy 500")
        self.starts += 1

    def stop(self, timeout=None) -> None:
        if self._fail:
            raise RuntimeError("proxy 500")
        self.stops += 1
        self.stop_timeouts.append(timeout)  # la maintenance en passe un long


class FakeBackup:
    def __init__(self, fail=False, on_trigger=None):
        self._fail = fail
        self.on_trigger = on_trigger
        self.triggers = 0
        self.running = False          # piloté par les tests (attente avant restauration)
        self.status_fail = False      # is_running() lève (proxy injoignable)
        self.exit_code_value: int | None = 0
        self.exit_code_fail = False

    def trigger(self) -> BackupResult:
        if self._fail:
            raise RuntimeError("conteneur de backup absent")
        self.triggers += 1
        if self.on_trigger is not None:
            self.on_trigger()
        return BackupResult(started=True, detail="sauvegarde one-shot démarrée")

    def is_running(self) -> bool:
        if self.status_fail:
            raise RuntimeError("état du conteneur illisible")
        return self.running

    def exit_code(self) -> int | None:
        if self.exit_code_fail:
            raise RuntimeError("résultat du conteneur illisible")
        return None if self.running else self.exit_code_value


class FakeBackupProfiles:
    """Fake BackupProfilesPort — en mémoire."""

    def __init__(self, profiles=None):
        self.profiles = {p.id: p for p in (profiles or [])}

    def list_profiles(self):
        return list(self.profiles.values())

    def get(self, profile_id):
        return self.profiles.get(profile_id)

    def upsert(self, profile):
        self.profiles[profile.id] = profile

    def delete(self, profile_id):
        return self.profiles.pop(profile_id, None) is not None

    def mark_run(self, profile_id, at):
        from dataclasses import replace
        if profile_id in self.profiles:
            self.profiles[profile_id] = replace(self.profiles[profile_id], last_run=at)


class FakeProfileBackup:
    """Fake ProfileBackupPort — enregistre les consignes."""

    def __init__(self, fail=False):
        self._fail = fail
        self.calls: list[dict] = []
        self.running = False
        self.status_fail = False
        self.exit_code_value: int | None = 0

    def trigger_profile(self, dest, name, includes, excludes):
        if self._fail:
            raise RuntimeError("conteneur de profil absent")
        self.calls.append({"dest": dest, "name": name, "includes": includes, "excludes": excludes})
        return BackupResult(started=True, detail=f"sauvegarde déclenchée ({name})")

    def is_running(self):
        if self.status_fail:
            raise RuntimeError("état illisible")
        return self.running

    def exit_code(self):
        return None if self.running else self.exit_code_value


class FakeBackupSchedule:
    """Fake BackupSchedulePort — en mémoire."""

    def __init__(self, hours=0.0, daily_at=None, last_run=None):
        self.hours = hours
        self.daily_at = daily_at
        self.last = last_run

    def get(self):
        from domain.model import BackupSchedule
        return BackupSchedule(hours=self.hours, daily_at=self.daily_at, last_run=self.last)

    def set_schedule(self, hours: float, daily_at=None) -> None:
        self.hours = hours if not daily_at else 0.0
        self.daily_at = daily_at

    def mark_run(self, at) -> None:
        self.last = at


class FakeRecurringRestart:
    """Fake RecurringRestartPort — en mémoire."""

    def __init__(self, config=None):
        self.config = config
        self.fired_day = None

    def get(self):
        return self.config

    def set_config(self, config) -> None:
        self.config = config
        self.fired_day = None

    def clear(self) -> None:
        self.config = None
        self.fired_day = None

    def last_fired(self):
        return self.fired_day

    def mark_fired(self, day: str) -> None:
        self.fired_day = day


class FakeArchiveChecks:
    """Fake ArchiveChecksPort — verdicts en mémoire."""

    def __init__(self, checks=None):
        self.checks = dict(checks or {})

    def statuses(self):
        return dict(self.checks)

    def record(self, check):
        self.checks[check.filename] = check
        self.checking_name = None

    def checking(self):
        return getattr(self, "checking_name", None)

    def mark_checking(self, filename):
        self.checking_name = filename

    def forget_missing(self, existing):
        self.checks = {k: v for k, v in self.checks.items() if k in existing}


class FakeArchiveValidator:
    """Fake ArchiveValidatorPort adossé aux métadonnées en mémoire."""

    def __init__(self, archives, results=None):
        self.archives = archives
        self.results = dict(results or {})
        self.calls: list[str] = []
        self.fail = None

    def validate(self, filename):
        self.calls.append(filename)
        if self.fail is not None:
            raise self.fail
        if filename in self.results:
            return self.results[filename]
        archive = next(
            (
                item
                for item in self.archives.list_archives()
                if item.filename == filename
            ),
            None,
        )
        if archive is None:
            return ArchiveCheck(
                filename=filename,
                ok=False,
                checked_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
                size_bytes=0,
                detail="archive introuvable",
                restorable=False,
            )
        return ArchiveCheck(
            filename=filename,
            ok=True,
            checked_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            size_bytes=archive.size_bytes,
            restorable=True,
            world_name="old_world_1",
            archive_created_at=archive.created_at,
        )


class FakeRestore:
    """Fake RestorePort — enregistre les archives cibles demandées."""

    def __init__(self, fail=False):
        self._fail = fail
        self.calls: list[str] = []
        self.running = False
        self.status_fail = False
        self.exit_code_value: int | None = 0
        self.exit_code_fail = False

    def restore(self, archive_relpath: str) -> None:
        self.calls.append(archive_relpath)
        if self._fail:
            raise RestoreUnavailable("mc-restore indisponible")

    def is_running(self) -> bool:
        if self.status_fail:
            raise RestoreUnavailable("état mc-restore illisible")
        return self.running

    def exit_code(self) -> int | None:
        if self.exit_code_fail:
            raise RestoreUnavailable("résultat mc-restore illisible")
        return None if self.running else self.exit_code_value


class FakeBackupArchives:
    def __init__(self, archives=None, paths=None):
        self._archives = list(archives) if archives is not None else [
            BackupArchive(filename="world-20260701-0000.tar.gz", size_bytes=772000000,
                          created_at=datetime(2026, 7, 1, tzinfo=timezone.utc)),
        ]
        self._paths = dict(paths or {})
        self.deleted: list[str] = []

    def list_archives(self) -> list[BackupArchive]:
        return list(self._archives)

    def archive_path(self, filename: str):
        if filename in self._paths:
            return self._paths[filename]
        for archive in self._archives:
            if archive.filename == filename:
                return f"/tmp/{filename}"
        return None

    def free_bytes(self):
        return 214 * 1024**3  # 214 Go libres (déterministe pour les tests)

    def delete_archive(self, filename: str) -> bool:
        if not (filename.startswith("scheduled/") or filename.startswith("profiles/")):
            return False  # même périmètre que l'adapter réel
        for archive in list(self._archives):
            if archive.filename == filename:
                self._archives.remove(archive)
                self.deleted.append(filename)
                return True
        return False


class FakeLogs:
    def __init__(self, lines=None):
        self._lines = list(lines or ["[INFO] ligne 1", "[INFO] ligne 2"])
        self.calls: list[int] = []

    def recent(self, lines: int = 100) -> list[str]:
        self.calls.append(lines)
        return list(self._lines[-lines:])


class FakeMetrics:
    def __init__(self, readings=None, available=True, perf=None):
        from domain.model import EntityTop, PerformanceSnapshot, WorldChunks
        self.perf = perf if perf is not None else PerformanceSnapshot(
            tps=20.0, mspt_mean=3.2, mspt_max=41.0,
            mspt_history=(2.0, 3.5, 60.0, 4.0),
            entities_total=245,
            entity_top=(EntityTop(world="overworld", type="zombie", count=120),
                        EntityTop(world="the_nether", type="ghast", count=14)),
            chunks=(WorldChunks(world="overworld", count=289, history=(250.0, 289.0)),
                    WorldChunks(world="the_end", count=0, history=None)),
        )
        self._init_readings(readings, available)

    def performance(self, history_hours=24):
        if not self._available:
            from domain.errors import ServerUnavailable
            raise ServerUnavailable("prometheus down (fake)")
        self.last_history_hours = history_hours
        return self.perf

    def _init_readings(self, readings=None, available=True):
        self.readings = list(readings) if readings is not None else [
            MetricReading(key="players", label="Joueurs en ligne", unit="", value=2.0),
            MetricReading(key="ram", label="RAM conteneur", unit="Mio", value=4500.0,
                          history=(4100.0, 4300.0, 4500.0)),
        ]
        self._available = available

    def snapshot(self) -> list[MetricReading]:
        if not self._available:
            raise ServerUnavailable("prometheus injoignable")
        return list(self.readings)


class FakeUpdater:
    def __init__(self, status=None, apply_fails=False):
        self.status = status or UpdateStatus(
            current_version="26.1",
            latest_version="26.2",
            update_available=True,
            changelog_url="https://minecraft.wiki/w/Java_Edition_26.2",
        )
        self._apply_fails = apply_fails
        self.applied = 0

    def check(self) -> UpdateStatus:
        return self.status

    def apply(self) -> None:
        if self._apply_fails:
            raise UpdateUnavailable("mc-updater absent")
        self.applied += 1


class FakeOps:
    """Fake OpsPort en lecture seule, indépendant de FakeGame.op_names.

    `entries` accepte des noms bruts (niveau 4 par défaut) ou des OpEntry
    déjà construits, pour ne pas alourdir les call sites existants."""

    def __init__(self, entries=None, available=True):
        self.entries: list[OpEntry] = [
            e if isinstance(e, OpEntry) else OpEntry(name=e, level=4) for e in (entries or [])
        ]
        self._available = available

    def list_ops(self) -> list[OpEntry]:
        if not self._available:
            raise ServerUnavailable("fichier ops.json illisible")
        return list(self.entries)


class FakePendingOpLevels:
    def __init__(self, entries=None, fail=False):
        self.entries: dict[str, PendingOpLevel] = {
            item.name.lower(): item for item in (entries or [])
        }
        self.fail = fail
        self.set_calls: list[tuple[str, int]] = []
        self.remove_calls: list[str] = []

    def list_pending(self) -> list[PendingOpLevel]:
        return sorted(self.entries.values(), key=lambda item: item.name.lower())

    def set_level(self, player_name: str, level: int) -> None:
        if self.fail:
            raise OSError("disque indisponible")
        self.set_calls.append((player_name, level))
        self.entries[player_name.lower()] = PendingOpLevel(player_name, level)

    def remove(self, player_name: str) -> None:
        if self.fail:
            raise OSError("disque indisponible")
        self.remove_calls.append(player_name)
        self.entries.pop(player_name.lower(), None)


class FakeOpLevelsApply:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0
        self.running = False
        self.exit_code_value: int | None = None

    def apply(self) -> None:
        if self.fail:
            raise ServerUnavailable("worker niveaux OP indisponible")
        self.calls += 1
        self.running = True
        self.exit_code_value = None

    def is_running(self) -> bool:
        if self.fail:
            raise ServerUnavailable("worker niveaux OP indisponible")
        return self.running

    def exit_code(self) -> int | None:
        if self.fail:
            raise ServerUnavailable("worker niveaux OP indisponible")
        return None if self.running else self.exit_code_value


class FakeBans:
    """Fake BansPort — indépendant de FakeGame.ban_calls (source réelle :
    banned-players.json, distincte du canal RCON qui écrit dedans)."""

    def __init__(self, entries=None, available=True):
        self.entries = list(entries) if entries is not None else [
            BanEntry(player="griefer", reason="Griefing spawn",
                     created_at=datetime(2026, 6, 1, tzinfo=timezone.utc), expires="forever"),
        ]
        self._available = available

    def list_bans(self) -> list[BanEntry]:
        if not self._available:
            raise ServerUnavailable("fichier banned-players.json illisible")
        return list(self.entries)


class FakeTempBans:
    """Fake TempBanPort — dict en mémoire {player: unban_at}."""

    def __init__(self):
        self._entries: dict[str, datetime] = {}

    def schedule(self, player: str, unban_at: datetime) -> None:
        self._entries[player] = unban_at

    def cancel(self, player: str) -> None:
        self._entries.pop(player, None)

    def pending(self) -> list[PendingUnban]:
        return [PendingUnban(player=p, unban_at=at) for p, at in self._entries.items()]

    def due(self, now: datetime) -> list[PendingUnban]:
        return [p for p in self.pending() if p.unban_at <= now]


class FakeBackgroundTask:
    """Remplace BackupScheduler/PlayerLogWatcher dans les tests API : évite de
    construire les vrais adapters (SqlitePlayerHistory touche le disque dès
    son __init__) juste pour satisfaire le lifespan FastAPI."""

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        pass


class FakePlayerHistory:
    def __init__(self, summary=None, sessions=None):
        self._summary = list(summary or [
            PlayerSummary(player="alice", total_seconds=3600.0, last_seen=datetime(2026, 7, 1, tzinfo=timezone.utc), online=False),
        ])
        self._sessions = list(sessions or [
            PlayerSession(player="alice",
                          joined_at=datetime(2026, 6, 30, 23, 0, tzinfo=timezone.utc),
                          left_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)),
        ])
        self.joins: list[tuple[str, datetime]] = []
        self.leaves: list[tuple[str, datetime]] = []
        self.commands = []
        # joueurs « déjà vus » (filtre nouveau joueur) : ceux du résumé initial
        self.known = {p.player for p in self._summary}

    def is_known(self, player):
        return player in self.known or any(name == player for name, _ in self.joins)

    def record_join(self, player: str, at: datetime) -> None:
        self.joins.append((player, at))

    def record_leave(self, player: str, at: datetime) -> None:
        self.leaves.append((player, at))

    def record_command(self, player, command, at):
        self.commands.append((player, at, command))

    def recent_commands(self, limit=100, since=None):
        return sorted(self.commands, key=lambda c: c[1], reverse=True)[:limit]

    def summary(self) -> list[PlayerSummary]:
        return list(self._summary)

    def recent_sessions(self, limit: int = 100, since=None):
        return list(self._sessions[:limit])


class FakeMods:
    """Fake ModsPort."""

    def __init__(self, mods=None):
        from datetime import datetime, timezone
        from domain.model import ModInfo
        self.mods = list(mods) if mods is not None else [
            ModInfo(filename="lithium-0.22.1.jar", size_bytes=1_048_576,
                    modified_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    name="Lithium", version="0.22.1", description="Optimisations générales.",
                    sha1="aaa111"),
            ModInfo(filename="mod-perso.jar", size_bytes=2048,
                    modified_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                    name="Mod perso", version="1.0", sha1="bbb222"),
        ]

    def list_mods(self):
        return list(self.mods)


class FakeServers:
    """Fake ServersPort."""

    def __init__(self, servers=None):
        self.servers = list(servers or [])

    def list_servers(self):
        return list(self.servers)

    def add(self, entry):
        if any(s.id == entry.id for s in self.servers):
            raise ValueError("doublon")
        self.servers.append(entry)

    def set_address(self, server_id, address):
        import dataclasses
        for i, srv in enumerate(self.servers):
            if srv.id == server_id:
                self.servers[i] = dataclasses.replace(srv, address=address)
                return True
        return False

    def set_map_url(self, server_id, map_url):
        import dataclasses
        for i, srv in enumerate(self.servers):
            if srv.id == server_id:
                self.servers[i] = dataclasses.replace(srv, map_url=map_url)
                return True
        return False


class FakeMapProbe:
    """Fake MapProbePort — verdict scripté, URLs testées enregistrées."""

    def __init__(self, ok=True, detail="la carte répond"):
        self.ok = ok
        self.detail = detail
        self.calls: list[str] = []

    def probe(self, url):
        self.calls.append(url)
        return self.ok, self.detail


class FakeAppUpdateState:
    """Fake AppUpdateStatePort — verdict en mémoire."""

    def __init__(self, release=None, checked_at=None):
        self.release = release
        self.checked_at = checked_at

    def get(self):
        if self.release is None or self.checked_at is None:
            return None
        return self.release, self.checked_at

    def set(self, release, checked_at):
        self.release = release
        self.checked_at = checked_at


class FakeAppUpdater:
    """Fake AppUpdaterPort — démarrages comptés, panne scriptable."""

    def __init__(self, fail=None):
        self.fail = fail
        self.starts = 0

    def start(self):
        if self.fail is not None:
            raise self.fail
        self.starts += 1


class FakeSelfImage:
    """Fake SelfImagePort — image du conteneur courant scriptée."""

    def __init__(self, value="ghcr.io/jmartinoty/mc-admin:0.9.0"):
        self.value = value

    def image(self):
        return self.value


class FakeDiscovery:
    """Fake ServerDiscoveryPort."""

    def __init__(self, detected=None):
        self.detected = list(detected or [])

    def discover(self, configured):
        from dataclasses import replace
        return [replace(d, configured=d.name in configured) for d in self.detected]

    def list_all(self):
        from domain.model import DetectedServer
        return list(self.detected) + [
            DetectedServer(name="playit", image="playit-agent:0.17", state="running", configured=False),
            DetectedServer(name="watchtower", image="watchtower:latest", state="running", configured=False),
        ]


class FakeNotifier:
    """Enregistre tout (aucun filtrage — le filtrage par événement vit dans
    StoreBackedNotifier, testé à part). 4-uplets : [::2] = (titre, niveau)
    reste vrai pour les assertions historiques."""

    def __init__(self):
        self.sent: list[tuple[str, str, str, str]] = []

    def notify(self, title: str, message: str, level: str = "info", event: str = "") -> None:
        self.sent.append((title, message, level, event))


class FakePlayerStats:
    """Fake PlayerStatsPort — stats vanilla en mémoire."""

    def __init__(self, stats=None):
        self.stats = list(stats) if stats is not None else [
            PlayerStats(player="alice", play_time_seconds=60480.0, deaths=14, mob_kills=91,
                        player_kills=1, distance_cm=3454828, jumps=6694,
                        blocks_mined=1234, items_crafted=210),
        ]

    def all_stats(self) -> list[PlayerStats]:
        return list(self.stats)


class RecordingAudit:
    def __init__(self):
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def recent(self, limit: int = 50) -> list[AuditEntry]:
        return list(self.entries[-limit:])


class FixedClock:
    """Horloge déterministe pour rendre l'audit vérifiable."""

    def __init__(self, moment=None):
        self._moment = moment or datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._moment


class MutableClock:
    """Comme FixedClock, mais avançable — pour simuler l'écoulement du temps
    entre plusieurs appels (ex. tick_scheduled_restart à différents instants)."""

    def __init__(self, moment=None):
        self.moment = moment or datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.moment


class FakeWatched:
    """Fake du store des compagnons surveillés (A2.1)."""

    def __init__(self, names=None):
        self.names = list(names or [])

    def all(self):
        return list(self.names)

    def add(self, name):
        if name in self.names:
            return False
        self.names.append(name)
        return True

    def remove(self, name):
        if name not in self.names:
            return False
        self.names.remove(name)
        return True


class FakeNotificationConfig:
    """Fake du store notifications.json V2 (canaux en profils)."""

    def __init__(self, channels=None):
        self._channels = [dict(c) for c in (channels or [])]
        self._mtime = 0.0

    def mtime(self):
        return self._mtime

    def _touch(self):
        self._mtime += 1

    def list_channels(self):
        return [dict(c) for c in self._channels]

    def get(self, channel_id):
        return next((dict(c) for c in self._channels if c["id"] == channel_id), None)

    def add_channel(self, type, label, config, events, enabled=True):
        base = label.lower().replace(" ", "-") or type
        cid, n = base, 2
        while any(c["id"] == cid for c in self._channels):
            cid, n = f"{base}-{n}", n + 1
        self._channels.append({"id": cid, "type": type, "label": label, "config": dict(config),
                               "events": dict(events), "enabled": enabled})
        self._touch()
        return cid

    def update_channel(self, channel_id, label=None, config=None, events=None, enabled=None):
        for c in self._channels:
            if c["id"] == channel_id:
                if label is not None:
                    c["label"] = label
                if config is not None:
                    c["config"] = dict(config)
                if events is not None:
                    c["events"] = dict(events)
                if enabled is not None:
                    c["enabled"] = enabled
                self._touch()
                return True
        return False

    def remove_channel(self, channel_id):
        before = len(self._channels)
        self._channels = [c for c in self._channels if c["id"] != channel_id]
        self._touch()
        return len(self._channels) < before


class FakeSpark:
    """Fake SparkProfilesPort."""

    def __init__(self, profiles=None):
        from domain.model import SparkProfile
        self.profiles = list(profiles or [
            SparkProfile(filename="profile-2026-07-17_17.06.46.sparkprofile",
                         size_bytes=19360,
                         created_at=datetime(2026, 7, 17, 15, 6, tzinfo=timezone.utc)),
        ])

    def list_profiles(self, limit=10):
        return list(self.profiles)[:limit]

    def path(self, filename):
        import re
        if not re.match(r"^profile-[\w.\-]+\.sparkprofile$", filename):
            return None
        if any(p.filename == filename for p in self.profiles):
            return __file__          # un vrai fichier lisible suffit au FileResponse
        return None


class FakeModChecks:
    """Fake ModChecksPort (verdicts Modrinth)."""

    def __init__(self, checks=None, checked=None):
        from domain.model import ModUpdate
        self.checks = dict(checks) if checks is not None else {
            "aaa111": ModUpdate(sha1="aaa111", known=True,
                                project_url="https://modrinth.com/project/lith",
                                installed_version="0.22.1", latest_version="0.23.0",
                                update_available=True),
            "bbb222": ModUpdate(sha1="bbb222", known=False),
        }
        self.checked = checked

    def statuses(self):
        return dict(self.checks)

    def checked_at(self):
        return self.checked

    def replace_all(self, checks, at):
        self.checks = dict(checks)
        self.checked = at


class FakeWorkerIntegrity:
    """WorkerIntegrityPort factice : problèmes injectés par famille."""

    def __init__(self, issues_by_kind=None, unknown_by_kind=None, checks=None):
        self._issues = issues_by_kind or {}
        self._unknown = unknown_by_kind or {}
        self._checks = checks or []

    def issues(self, kind, *, include_unknown=False):
        out = list(self._issues.get(kind, ()))
        if include_unknown:
            out.extend(self._unknown.get(kind, ()))
        return out

    def statuses(self):
        return list(self._checks)


class FakeDoorman:
    """DoormanPort en mémoire. `running` est l'état RÉEL observé (le service
    ne déduit jamais la maintenance d'autre chose que de cette lecture)."""

    def __init__(self, running=False, fail_engage=False, fail_release=False):
        self.running = running
        self.fail_engage = fail_engage
        self.fail_release = fail_release
        self.status_fail = False       # is_running() lève (proxy injoignable)
        self.consignes = []            # (motd, kick) de chaque prise de poste
        self.releases = 0

    def engage(self, motd: str, kick: str) -> None:
        if self.fail_engage:
            raise MaintenanceUnavailable("portier non démarrable")
        self.consignes.append((motd, kick))
        self.running = True

    def release(self) -> None:
        self.releases += 1
        if self.fail_release:
            raise MaintenanceUnavailable("le portier tient toujours l'adresse")
        self.running = False

    def is_running(self) -> bool:
        if self.status_fail:
            raise MaintenanceUnavailable("état du portier illisible")
        return self.running
