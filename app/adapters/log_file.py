"""Adapter LogPort : lit les dernières lignes du fichier de log monté en RO.

On lit `latest.log` de Minecraft, monté en lecture seule (cf. CLAUDE.md §8) :
pas de `docker logs`, pas de `docker exec`, pas de RCON.

Filtre de bruit : mc-admin lui-même génère 2 lignes de log RCON par tick de
polling (« Thread RCON Client … started/shutting down ») — la vue serait
illisible. Les lignes correspondant à `exclude` sont écartées AVANT le tail,
pour renvoyer N lignes UTILES ; le fichier n'est pas modifié. Motif
configurable via LOG_EXCLUDE (vide = pas de filtre).

Historique à travers les rotations : Minecraft archive `latest.log` en
`YYYY-MM-DD-N.log.gz` dans le même dossier. Quand `latest.log` filtré ne
suffit pas à remplir la demande (typiquement juste après une rotation), on
complète en remontant dans les archives, de la plus récente à la plus
ancienne. Pas de base de données : les archives SONT l'historique, déjà
écrites par le serveur. Chaque archive étant immuable, son contenu filtré
est mis en cache (clé mtime) — la décompression n'est payée qu'une fois,
pas à chaque poll de 5 s.
"""
from __future__ import annotations

import gzip
import re
from collections import deque
from pathlib import Path

# Bruit des threads RCON (générés par mc-admin et mc-backup eux-mêmes).
DEFAULT_EXCLUDE = r"Thread RCON (Listener|Client)"

# Première ligne d'un démarrage serveur : Fabric ("Loading Minecraft X with
# Fabric Loader") ou vanilla ("Starting minecraft server version X"). Sert à
# borner l'affichage au DERNIER démarrage plutôt qu'à un simple compte de lignes.
BOOT_MARKER = r"Loading Minecraft .+ with Fabric Loader|Starting minecraft server version"

# Bornes : nombre max d'archives consultées par appel (latence du 1er appel)
# et lignes filtrées conservées en cache par archive (mémoire).
_MAX_ARCHIVES = 7
_CACHE_LINES_PER_ARCHIVE = 500


class FileLog:
    def __init__(self, path: str, exclude: str | None = DEFAULT_EXCLUDE,
                 boot_marker: str | None = BOOT_MARKER) -> None:
        self._path = Path(path)
        self._exclude = re.compile(exclude) if exclude else None
        self._boot = re.compile(boot_marker) if boot_marker else None
        # {chemin: (mtime, lignes filtrées)} — une archive est immuable, son
        # mtime ne change pas ; si le fichier est remplacé, le cache se rafraîchit.
        self._archive_cache: dict[str, tuple[float, list[str]]] = {}

    def _keep(self, line: str) -> bool:
        return self._exclude is None or not self._exclude.search(line)

    def recent(self, lines: int = 100) -> list[str]:
        """Lignes depuis le DERNIER démarrage du serveur, plafonnées à `lines`.

        Le marqueur de boot est cherché dans latest.log puis, s'il n'y est pas
        (rotation depuis le démarrage), en remontant dans les archives. Sans
        marqueur trouvé : simple plafond de `lines` (comportement historique)."""
        if lines <= 0:
            return []
        current = self._read_latest(lines)
        cut = self._last_boot_index(current)
        if cut is not None:
            return current[cut:]
        missing = lines - len(current)
        if missing <= 0:
            return current
        return self._backfill_from_archives(missing) + current

    def _last_boot_index(self, lines_list: list[str]) -> int | None:
        if self._boot is None:
            return None
        for i in range(len(lines_list) - 1, -1, -1):
            if self._boot.search(lines_list[i]):
                return i
        return None

    def _read_latest(self, lines: int) -> list[str]:
        try:
            # errors="replace" : les logs peuvent contenir des octets non-UTF-8.
            with open(self._path, encoding="utf-8", errors="replace") as fh:
                tail = deque((line for line in fh if self._keep(line)), maxlen=lines)
        except (FileNotFoundError, IsADirectoryError):
            # latest.log peut ne pas exister encore (serveur jamais démarré).
            return []
        return [line.rstrip("\n") for line in tail]

    def _backfill_from_archives(self, missing: int) -> list[str]:
        prefix: list[str] = []
        for archive in self._archives_newest_first():
            if missing <= 0:
                break
            archived = self._archive_lines(archive)
            cut = self._last_boot_index(archived)
            if cut is not None:
                # Démarrage trouvé dans CETTE archive : on prend depuis lui et on
                # s'arrête — pas de logs de l'exécution précédente du serveur.
                take = archived[cut:][-missing:]
                return take + prefix
            take = archived[-missing:] if archived else []
            prefix = take + prefix  # l'archive plus ancienne passe DEVANT
            missing -= len(take)
        return prefix

    def _archives_newest_first(self) -> list[Path]:
        try:
            archives = sorted(
                self._path.parent.glob("*.log.gz"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        return archives[:_MAX_ARCHIVES]

    def _archive_lines(self, archive: Path) -> list[str]:
        key = str(archive)
        try:
            mtime = archive.stat().st_mtime
        except OSError:
            return []
        cached = self._archive_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            with gzip.open(archive, "rt", encoding="utf-8", errors="replace") as fh:
                tail = deque(
                    (line.rstrip("\n") for line in fh if self._keep(line)),
                    maxlen=_CACHE_LINES_PER_ARCHIVE,
                )
        except OSError:  # gz corrompu / illisible : on l'ignore
            return []
        result = list(tail)
        self._archive_cache[key] = (mtime, result)
        return result
