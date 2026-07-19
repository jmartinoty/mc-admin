"""AdminService : orchestrateur métier — façade assemblée (découpage V7.0).

Import public INCHANGÉ (`from domain.services import AdminService`) : le corps
vit dans des modules par thème, mélangés (mixins) dans la façade. La politique
RBAC/audit et les constantes partagées vivent dans base.py (ServiceCore).
"""
from __future__ import annotations

from domain.services.accounts import AccountsMixin
from domain.services.actions import ActionsMixin
from domain.services.backups import BackupsMixin
from domain.services.base import (  # noqa: F401 — ré-exports publics
    EVENT_KEYS,
    EVENT_LEGACY_DEFAULTS,
    NOTIFICATION_CHANNEL_TYPES,
    ServiceCore,
)
from domain.services.game import GameMixin
from domain.services.moderation import ModerationMixin
from domain.services.monitoring import MonitoringMixin
from domain.services.servers import ServersMixin
from domain.services.update import UpdateMixin


class AdminService(
    MonitoringMixin,
    ServersMixin,
    AccountsMixin,
    ActionsMixin,
    BackupsMixin,
    GameMixin,
    UpdateMixin,
    ModerationMixin,
    ServiceCore,
):
    """Façade : RBAC deny-by-default + audit + orchestration des ports."""
