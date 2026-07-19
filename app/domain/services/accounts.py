"""Comptes : barrière USER_MANAGE + traces d'audit des mutations.

Découpage de services.py (V7.0) — corps INCHANGÉ, RBAC/audit via
ServiceCore (base.py), assemblé dans la façade AdminService (__init__.py).
"""
from __future__ import annotations


from domain.model import (
    AuditEntry,
    Permission,
    User,
)


# Format officiel des pseudos Minecraft. Validé avant TOUT envoi RCON.



class AccountsMixin:
    def authorize_user_management(self, user: User) -> None:
        """Barrière RBAC de la page Comptes (le stockage des comptes vit côté
        API — sessions, hashs — mais le refus doit être audité comme partout)."""
        self._authorize(user, Permission.USER_MANAGE)

    def record_account_change(self, actor: User, detail: str) -> None:
        """Trace une mutation de compte (création, reset, suppression) —
        l'action elle-même est mécanique côté API, l'audit vit ici."""
        self._authorize(actor, Permission.USER_MANAGE)
        self._record(actor, Permission.USER_MANAGE, "allowed", detail)

    def record_first_account(self, username: str) -> None:
        """Trace la création du compte administrateur au PREMIER lancement
        (V6.2) — événement d'amorçage unique, hors RBAC : il n'existe encore
        aucun compte pour le porter."""
        self._audit.record(AuditEntry(
            timestamp=self._clock.now(), username=username, role="owner",
            action="SETUP", target="mc-admin", outcome="allowed",
            detail=f"phase=first_account username={username}",
        ))

