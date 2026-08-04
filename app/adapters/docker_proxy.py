"""Adapter ContainerPort : cycle de vie du conteneur via docker-socket-proxy.

docker-py est pointé sur le PROXY via DOCKER_HOST (jamais le socket brut,
cf. CLAUDE.md §7.1). Le client est importé paresseusement (tests = client factice).

Garde-fou contre le risque résiduel du proxy (il filtre par catégorie d'API,
pas par nom de conteneur) : l'adapter est lié à UN conteneur (`container_name`)
et refuse explicitement toute autre cible via `ensure_authorized`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from domain.errors import ServerUnavailable, UnauthorizedContainer
from domain.model import DetectedServer
from domain.model import ContainerState


def _explain(exc: Exception) -> str:
    """Message court et lisible d'une erreur docker-py.

    `APIError` porte une `explanation` déjà en clair (ex. « Address already in
    use ») ; sa représentation complète, elle, est un pavé HTTP avec l'URL et
    l'ID du conteneur — inutile pour l'utilisateur."""
    explanation = getattr(exc, "explanation", None)
    if explanation:
        return str(explanation).strip('"() ')
    return str(exc)


def ensure_authorized(requested: str, allowed: str) -> None:
    """Refuse explicitement toute cible ≠ conteneur autorisé (garde-fou §7.1)."""
    if requested != allowed:
        raise UnauthorizedContainer(
            f"cible '{requested}' interdite (seul '{allowed}' est autorisé)"
        )


def _parse_started_at(raw) -> "datetime | None":
    """StartedAt docker : RFC3339 avec nanosecondes ("…12.123456789Z") —
    fromisoformat n'accepte que 6 décimales, on tronque. Jamais démarré :
    docker renvoie l'an 1 ("0001-01-01…"), traité comme absent."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        text = raw.rstrip("Z")
        if "." in text:
            head, frac = text.split(".", 1)
            text = f"{head}.{frac[:6]}"
        started = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return started if started.year > 1 else None
    except ValueError:
        return None


class DockerProxyContainer:
    def __init__(self, container_name: str, client=None) -> None:
        self._name = container_name
        self._client = client  # None -> résolu paresseusement via DOCKER_HOST

    def _get_client(self):
        if self._client is None:
            import docker  # import paresseux ; from_env lit DOCKER_HOST (=proxy)
            self._client = docker.from_env()
        return self._client

    def _target(self):
        container = self._get_client().containers.get(self._name)
        # Défense en profondeur : on ne pilote QUE le conteneur configuré.
        ensure_authorized(getattr(container, "name", self._name), self._name)
        return container

    def status(self) -> ContainerState:
        try:
            c = self._target()
            state = (getattr(c, "attrs", None) or {}).get("State", {})
            health = state.get("Health", {}).get("Status") if isinstance(state.get("Health"), dict) else None
            return ContainerState(
                name=self._name,
                running=(c.status == "running"),
                status=str(c.status),
                health=health,
                started_at=_parse_started_at(state.get("StartedAt")),
            )
        except UnauthorizedContainer:
            raise
        except Exception as exc:  # noqa: BLE001 — lecture : dégradation propre
            raise ServerUnavailable(f"conteneur '{self._name}' injoignable via le proxy") from exc

    # Actions mutantes : l'échec est TRADUIT en erreur métier (le service
    # l'audite puis la relaie, et la couche web sait l'afficher). Laisser
    # passer une exception docker-py brute produisait une 500 illisible —
    # constaté le 31/07 sur « Démarrer » pendant une maintenance : Docker
    # refuse l'adresse déjà prise par le portier, l'utilisateur voyait
    # « Internal Server Error » sans savoir quoi faire.
    def _do(self, action: str, verb: str, *args, **kwargs) -> None:
        try:
            getattr(self._target(), action)(*args, **kwargs)
        except UnauthorizedContainer:
            raise
        except ServerUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — traduit, jamais masqué
            raise ServerUnavailable(
                f"{verb} du conteneur '{self._name}' refusé par Docker : {_explain(exc)}"
            ) from exc

    def restart(self) -> None:
        self._do("restart", "redémarrage")

    def start(self) -> None:
        self._do("start", "démarrage")

    def stop(self, timeout: int | None = None) -> None:
        # timeout=None -> défaut docker-py (10 s). L'arrêt de maintenance passe
        # une valeur bien plus large : Minecraft sauvegarde ses mondes à
        # l'arrêt et se faisait tuer au SIGKILL avant la fin (spike 19/07/2026).
        if timeout is None:
            self._do("stop", "arrêt")
        else:
            self._do("stop", "arrêt", timeout=timeout)


class DockerServerDiscovery:
    """ServerDiscoveryPort en mode Docker (via le proxy, droits existants)."""

    def __init__(self, client=None) -> None:
        self._client = client

    def discover(self, configured: set[str]) -> list[DetectedServer]:
        return discover_minecraft_containers(configured, self._client)

    def list_all(self) -> list[DetectedServer]:
        """TOUS les conteneurs de l'hôte (candidats à la surveillance A2.1) —
        même listage autorisé par le proxy, même best-effort."""
        try:
            client = self._client
            if client is None:
                import docker
                client = docker.from_env()
            containers = client.containers.list(all=True)
        except Exception:  # noqa: BLE001
            return []
        out: list[DetectedServer] = []
        for container in containers:
            image = ""
            try:
                tags = getattr(container.image, "tags", None) or []
                image = tags[0] if tags else str(getattr(container.image, "id", ""))[:19]
            except Exception:  # noqa: BLE001
                pass
            out.append(DetectedServer(
                name=getattr(container, "name", "?"),
                image=image,
                state=str(getattr(container, "status", "?")),
                configured=False,
            ))
        return sorted(out, key=lambda d: d.name)


def discover_minecraft_containers(configured: set[str], client=None) -> list[DetectedServer]:
    """Repère les conteneurs qui ressemblent à un serveur Minecraft (image
    contenant « minecraft », ou port 25565 exposé). N'utilise QUE le listage
    autorisé par le proxy (CONTAINERS=1) — aucun droit supplémentaire.
    Best-effort : proxy injoignable = liste vide, jamais d'erreur."""
    try:
        if client is None:
            import docker
            client = docker.from_env()
        containers = client.containers.list(all=True)
    except Exception:  # noqa: BLE001
        return []
    found: list[DetectedServer] = []
    for container in containers:
        image = ""
        try:
            tags = getattr(container.image, "tags", None) or []
            image = tags[0] if tags else str(getattr(container.image, "id", ""))[:19]
        except Exception:  # noqa: BLE001
            pass
        ports = (getattr(container, "attrs", {}) or {}).get("NetworkSettings", {}).get("Ports") or {}
        looks_minecraft = "minecraft-server" in image or any(p.startswith("25565/") for p in ports)
        if not looks_minecraft:
            continue
        name = getattr(container, "name", "")
        found.append(DetectedServer(
            name=name, image=image, state=getattr(container, "status", "?"),
            configured=name in configured,
        ))
    return found
