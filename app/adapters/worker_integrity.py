"""Vérification d'alignement des conteneurs one-shot pré-créés.

mc-admin ne peut pas CRÉER de conteneurs (choix de sécurité §7.1 : le proxy
n'autorise que start/stop/restart/inspection). Les one-shots (mc-restore,
mc-op-levels, mc-backup-*…) sont donc créés à l'avance par un humain — et un
conteneur créé fige son image ET ses réseaux à cet instant. Après un rebuild
de l'image ou une recréation de réseau, le one-shot exécuterait du code
périmé ou échouerait en 404 au démarrage (trois incidents le 18/07/2026,
dont la sauvegarde nocturne).

Cet adapter compare, via les inspections DÉJÀ autorisées par le proxy
(CONTAINERS=1, aucun droit élargi) :
- l'image du one-shot à celle du conteneur mc-admin courant (pour ceux qui
  partagent son image : mc-restore, mc-op-levels) ;
- les réseaux du one-shot à ceux de mc-admin, PAR NOM : deux conteneurs
  branchés sur le même nom de réseau doivent voir le même NetworkID — le
  proxy interdit /networks (NETWORKS=0), cette comparaison croisée est donc
  le seul moyen de détecter un réseau recréé, et il suffit.

mc-admin identifie son propre conteneur par son hostname (= id court du
conteneur, comportement Docker standard).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from domain.model import WorkerCheck


@dataclass(frozen=True)
class WorkerSpec:
    """Un one-shot à surveiller. `kinds` = familles d'opérations bloquées
    par un désalignement ("restore", "backup") ; vide = diagnostic seul."""

    name: str
    label: str
    same_image: bool = False
    kinds: frozenset[str] = field(default_factory=frozenset)


_RECREATE_HINT = "docker compose --profile tools create --force-recreate"


class DockerWorkerIntegrity:
    def __init__(
        self,
        specs: list[WorkerSpec],
        self_container_id: str | None = None,
        client=None,
    ) -> None:
        self._specs = list(specs)
        self._self_id = self_container_id or os.environ.get("HOSTNAME", "")
        self._client = client

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux ; from_env lit DOCKER_HOST (=proxy)

            self._client = docker.from_env()
        return self._client

    @staticmethod
    def _container_facts(container) -> tuple[str, dict[str, str]]:
        attrs = getattr(container, "attrs", None) or {}
        image = str(attrs.get("Image", ""))
        raw_networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
        networks = {}
        for name, config in raw_networks.items():
            if isinstance(config, dict):
                networks[str(name)] = str(config.get("NetworkID") or "")
        return image, networks

    def _self_facts(self) -> tuple[str, dict[str, str]] | None:
        if not self._self_id:
            return None
        try:
            return self._container_facts(
                self._get_client().containers.get(self._self_id)
            )
        except Exception:  # noqa: BLE001 — invérifiable, jamais une exception
            return None

    def _check_spec(
        self,
        spec: WorkerSpec,
        self_facts: tuple[str, dict[str, str]] | None,
    ) -> WorkerCheck:
        try:
            container = self._get_client().containers.get(spec.name)
        except Exception as exc:  # noqa: BLE001
            not_found = "404" in str(exc) or "not found" in str(exc).lower()
            if not_found:
                return WorkerCheck(
                    spec.name, spec.label, False,
                    f"conteneur absent — le créer une fois : "
                    f"docker compose --profile tools create {spec.name}",
                )
            return WorkerCheck(
                spec.name, spec.label, None,
                "vérification impossible (proxy Docker muet)",
            )
        if self_facts is None:
            return WorkerCheck(
                spec.name, spec.label, None,
                "vérification impossible (conteneur mc-admin introuvable "
                "via le proxy)",
            )

        worker_image, worker_networks = self._container_facts(container)
        self_image, self_networks = self_facts

        if spec.same_image and worker_image and self_image and worker_image != self_image:
            return WorkerCheck(
                spec.name, spec.label, False,
                f"périmé : il utilise une ancienne image de mc-admin — "
                f"{_RECREATE_HINT} {spec.name}",
            )
        for net_name, worker_net_id in worker_networks.items():
            self_net_id = self_networks.get(net_name, "")
            if worker_net_id and self_net_id and worker_net_id != self_net_id:
                return WorkerCheck(
                    spec.name, spec.label, False,
                    f"branché sur un réseau « {net_name} » qui n'existe plus — "
                    f"{_RECREATE_HINT} {spec.name}",
                )
        return WorkerCheck(spec.name, spec.label, True, "aligné avec mc-admin")

    def statuses(self) -> list[WorkerCheck]:
        self_facts = self._self_facts()
        return [self._check_spec(spec, self_facts) for spec in self._specs]

    def issues(self, kind: str, *, include_unknown: bool = False) -> list[str]:
        self_facts = self._self_facts()
        out: list[str] = []
        for spec in self._specs:
            if kind not in spec.kinds:
                continue
            check = self._check_spec(spec, self_facts)
            if check.ok is False or (check.ok is None and include_unknown):
                out.append(f"{check.label} : {check.detail}")
        return out
