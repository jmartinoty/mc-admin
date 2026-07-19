"""Adapter : serveurs administrés, persistés en JSON (V6.3).

`/data/servers.json` liste les serveurs. Pour l'instant seule la PREMIÈRE
entrée est réellement pilotée (les adapters sont construits au démarrage
depuis elle) ; les entrées suivantes attendent la V7 (multi-serveur + mode
hôte). La MIGRATION depuis le monde d'avant (tout en variables d'env) se
fait au premier chargement. Jamais de secret ici : RCON_PASSWORD reste env.
"""
from __future__ import annotations

import os

from adapters.atomic_json import (
    CorruptJsonError,
    atomic_write_json,
    load_json,
    shared_path_lock,
)
from domain.model import ServerEntry


class JsonServers:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = shared_path_lock(path)

    @staticmethod
    def _validate_raw(raw: dict) -> None:
        if (
            not isinstance(raw.get("id"), str)
            or not raw["id"]
            or any(
                not isinstance(raw.get(key, default), str)
                for key, default in (
                    ("name", raw["id"]),
                    ("mode", "docker"),
                    ("container", ""),
                    ("rcon_host", ""),
                    ("log_file", ""),
                    ("world_dir", "world"),
                    ("address", ""),
                    ("map_url", ""),
                )
            )
            or not (
                isinstance(raw.get("rcon_port", 25575), int)
                and not isinstance(raw.get("rcon_port", 25575), bool)
            )
        ):
            raise TypeError

    def _load(self, *, strict: bool = False) -> list[dict]:
        data = load_json(
            self._path,
            expected_type=dict,
            default_factory=dict,
            strict=strict,
        )
        servers = data.get("servers", [])
        if not isinstance(servers, list):
            if strict:
                raise CorruptJsonError(
                    f"schéma JSON inattendu : {self._path}"
                )
            return []
        valid: list[dict] = []
        seen: set[str] = set()
        for raw in servers:
            try:
                if not isinstance(raw, dict):
                    raise TypeError
                self._validate_raw(raw)
                self._to_entry(raw)
                if strict and raw["id"] in seen:
                    raise TypeError
            except (KeyError, TypeError, ValueError):
                if strict:
                    raise CorruptJsonError(
                        f"schéma JSON inattendu : {self._path}"
                    ) from None
                continue
            seen.add(raw["id"])
            valid.append(raw)
        return valid

    def _write(self, servers: list[dict]) -> None:
        atomic_write_json(
            self._path,
            {"servers": servers},
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def _to_entry(raw: dict) -> ServerEntry:
        return ServerEntry(
            id=raw["id"],
            name=raw.get("name", raw["id"]),
            mode=raw.get("mode", "docker"),
            container=raw.get("container", ""),
            rcon_host=raw.get("rcon_host", ""),
            rcon_port=int(raw.get("rcon_port", 25575)),
            log_file=raw.get("log_file", ""),
            world_dir=raw.get("world_dir", "world"),
            address=raw.get("address", ""),
            map_url=raw.get("map_url", ""),
        )

    def list_servers(self) -> list[ServerEntry]:
        return [self._to_entry(raw) for raw in self._load()]

    def add(self, entry: ServerEntry) -> None:
        with self._lock:
            servers = self._load(strict=True)
            if any(raw["id"] == entry.id for raw in servers):
                raise ValueError(f"serveur déjà enregistré : {entry.id}")
            servers.append({
                "id": entry.id, "name": entry.name, "mode": entry.mode,
                "container": entry.container, "rcon_host": entry.rcon_host,
                "rcon_port": entry.rcon_port, "log_file": entry.log_file,
                "world_dir": entry.world_dir, "address": entry.address,
                "map_url": entry.map_url,
            })
            self._write(servers)

    def set_address(self, server_id: str, address: str) -> bool:
        with self._lock:
            servers = self._load(strict=True)
            for raw in servers:
                if raw["id"] == server_id:
                    raw["address"] = address
                    self._write(servers)
                    return True
            return False

    def set_map_url(self, server_id: str, map_url: str) -> bool:
        with self._lock:
            servers = self._load(strict=True)
            for raw in servers:
                if raw["id"] == server_id:
                    raw["map_url"] = map_url
                    self._write(servers)
                    return True
            return False

    def seed_from_env(self, settings) -> bool:
        """Migration V6.3 : la configuration env existante devient le premier
        serveur. Idempotent (fichier présent = rien à faire) ; une install
        fraîche sans conteneur configuré ne crée rien (l'assistant s'en charge)."""
        with self._lock:
            if os.path.exists(self._path) or not settings.minecraft_container:
                return False
            entry = ServerEntry(
                id="principal", name="Serveur principal", mode="docker",
                container=settings.minecraft_container,
                rcon_host=settings.rcon_host, rcon_port=settings.rcon_port,
                log_file=settings.mc_log_file, world_dir=settings.world_dir,
            )
            self._write([{
                "id": entry.id, "name": entry.name, "mode": entry.mode,
                "container": entry.container, "rcon_host": entry.rcon_host,
                "rcon_port": entry.rcon_port, "log_file": entry.log_file,
                "world_dir": entry.world_dir, "address": entry.address,
            }])
            return True
