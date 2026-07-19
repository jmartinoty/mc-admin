"""Adapters concrets des ports du domaine.

Chaque adapter implémente structurellement un Protocol de `domain.ports`. Les
dépendances lourdes (mcrcon, docker) sont importées paresseusement pour rester
testables sans ces libs.
"""
from .audit_file import FileAuditLog
from .backup import DockerBackupTrigger
from .backup_archives import FileBackupArchives
from .bans_file import FileBans
from .clock import SystemClock
from .docker_proxy import DockerProxyContainer, ensure_authorized
from .log_file import FileLog
from .notifications import CompositeNotifier, DiscordWebhookNotifier, TelegramNotifier
from .ops_file import OpsFile
from .player_history import SqlitePlayerHistory
from .prometheus import PrometheusMetrics
from .rcon import RconGame, parse_player_list
from .temp_bans import JsonTempBans

__all__ = [
    "FileAuditLog",
    "FileLog",
    "RconGame",
    "parse_player_list",
    "DockerProxyContainer",
    "ensure_authorized",
    "DockerBackupTrigger",
    "SystemClock",
    "PrometheusMetrics",
    "OpsFile",
    "DiscordWebhookNotifier",
    "TelegramNotifier",
    "CompositeNotifier",
    "SqlitePlayerHistory",
    "FileBackupArchives",
    "FileBans",
    "JsonTempBans",
]
