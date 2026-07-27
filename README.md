# mc-admin

Interface d'administration web pour serveur Minecraft conteneurisé —
pensée pour les serveurs entre amis hébergés sur un NAS ou un petit
serveur maison.

Tableau de bord temps réel, gestion des joueurs (whitelist, op, kick,
bans temporisés), sauvegardes par profils avec rétention automatique,
restauration en un clic avec filet de sécurité, mise à jour contrôlée du
serveur, réglages de jeu, page mods, comptes multi-rôles et journal
d'audit complet — le tout en français.

## Pourquoi mc-admin ?

La plupart des panels demandent un accès direct au socket Docker — c'est
l'équivalent de root sur la machine. mc-admin fait le pari inverse :

- **Jamais le socket.** L'app parle à un
  [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
  en whitelist minimale : lister, inspecter, démarrer/arrêter. Rien
  d'autre — pas d'exec, pas de création, pas d'images. Et elle refuse
  toute cible autre que les conteneurs qu'elle administre.
- **Les opérations privilégiées sont des conteneurs one-shot dédiés**
  (sauvegarde, restauration, mise à jour), inertes au repos. mc-admin
  leur écrit une *consigne* validée dans un fichier, puis les démarre via
  le proxy. Aucune commande ne transite ; les commandes sensibles sont
  figées dans le compose.
- **RBAC + audit.** Chaque action passe par une permission (rôles
  owner/admin/friend par défaut, personnalisables) et laisse une trace
  horodatée dans le journal, y compris les refus. Sessions signées,
  mots de passe Argon2, cookies `HttpOnly/SameSite`, CSRF sur tous les
  POST, anti-bruteforce au login. RCON jamais publié hors du réseau
  Docker.
- **Lecture seule par défaut.** Logs, bans, mods, archives : montés en
  `ro`. La rétention ne peut physiquement supprimer que les sauvegardes
  automatiques — jamais les manuelles.

Architecture hexagonale (domaine sans I/O, ports/adapters), stdlib
autant que possible, ~790 tests.

## Prérequis

- Docker + Docker Compose sur l'hôte.
- Un serveur Minecraft conteneurisé — l'image
  [itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server)
  est la référence — **démarré au moins une fois** (ses fichiers
  `ops.json`/`banned-players.json` doivent exister) et avec **RCON
  activé** (port non publié : mc-admin le joint par le réseau Docker).
- Facultatif : Prometheus pour le panneau métriques (TPS, MSPT, CPU,
  RAM…) et la détection de version.

## Installation

```sh
git clone https://github.com/jmartinoty/mc-admin.git
cd mc-admin
cp docker-compose.example.yml docker-compose.yml
cp .env.example .env
# Remplir .env : SESSION_SECRET, RCON_PASSWORD, chemins, réseau…
docker compose up -d --build
docker compose --profile tools create mc-backup-profile mc-backup-safety mc-updater mc-restore mc-op-levels
# Après une mise à jour, recréer les workers qui utilisent l'image mc-admin :
docker compose --profile tools create --force-recreate mc-restore mc-op-levels
```

BlueMap peut être ajouté comme service facultatif sans publier son port 8100.
La page Carte de mc-admin reste alors l'unique accès HTTP authentifié. Voir
[le guide BlueMap](docs/bluemap.md).

Puis ouvrir `http://<hôte>:8080` :

1. l'assistant de premier lancement crée le compte administrateur ;
2. la page **Serveurs** détecte les conteneurs Minecraft de l'hôte et
   vérifie chaque branchement en direct (proxy, RCON, logs, volume de
   sauvegardes, Prometheus) avec le correctif à appliquer en clair ;
3. redémarrer mc-admin une fois le serveur ajouté — tout le reste se
   règle dans l'interface.

## Configuration

Tout vit dans `.env` (voir [.env.example](.env.example)) :

| Variable | Rôle |
|---|---|
| `SESSION_SECRET` | clé de signature des sessions (`openssl rand -hex 32`) — **requis** |
| `RCON_PASSWORD` | mot de passe RCON du serveur Minecraft — **requis** |
| `MC_DATA_DIR` | dossier de données du serveur (le `/data` d'itzg) |
| `BACKUPS_DIR` | dossier des archives de sauvegarde |
| `MC_NETWORK` | réseau docker existant où le serveur est joignable |
| `MC_ADMIN_PORT` | port HTTP de l'interface (défaut 8080) |
| `MC_ADMIN_IMAGE` | image utilisée par mc-admin et le restaurateur (défaut `mc-admin:latest`) |
| `MC_STARTUP_TIMEOUT_SECONDS` | délai du healthcheck Minecraft avant rollback (défaut 300 s) |
| `BLUEMAP_*` / `MC_WORLD_DIR` | image, volumes et ressources du service BlueMap facultatif |
| `MC_COMPOSE_DIR` / `MC_COMPOSE_PROJECT` / `MC_SERVICE` / `MC_CONTAINER` | pour la mise à jour contrôlée et la restauration |
| `PROMETHEUS_URL` | facultatif — panneau métriques et page mise à jour |
| `DISCORD_WEBHOOK_URL`, `TELEGRAM_*` | facultatif — notifications |

Les comptes, rôles des utilisateurs, serveurs administrés, profils de
sauvegarde et planifications se gèrent **dans l'interface** et persistent
dans `./data`. Deux fichiers optionnels dans `./config` pour aller plus
loin : `roles.yml` (rôles/permissions personnalisés — voir
[config/roles.example.yml](config/roles.example.yml)) et `metrics.yml`
(métriques personnalisées — voir
[config/metrics.example.yml](config/metrics.example.yml) ; sans lui,
défauts embarqués : joueurs, latence, CPU, RAM, TPS, MSPT).

## Sauvegardes

Les sauvegardes sont des **profils** créés dans l'interface : chacun a
son contenu (monde, mods, configurations, listes de joueurs, ou tout),
sa destination, sa planification (intervalle ou heure quotidienne) et sa
rétention (N jours + N archives mensuelles). Un conteneur générique
unique les exécute tous ; l'intégrité de chaque archive est vérifiée en
tâche de fond.

Avant toute restauration, une sauvegarde de sécurité vérifiée est déclenchée
automatiquement (conteneur dédié, dossier auto-purgé, hors de portée
d'écriture de mc-admin). Le restaurateur prépare ensuite l'archive sans
arrêter Minecraft, clone le monde courant dans un espace de retour arrière,
puis remplace son contenu. Le rollback n'est supprimé qu'après le healthcheck
Docker de Minecraft. En cas d'échec pendant le remplacement ou le redémarrage,
il remet automatiquement l'ancien monde en place. La sauvegarde
de sécurité reste utile comme copie durable si l'hôte ou le stockage tombe en
panne pendant cette transaction locale.

Les commandes `op` et `deop` restent immédiates via RCON. Un niveau OP
individuel différent du niveau global est conservé dans `./data`, puis appliqué
pendant le prochain redémarrage demandé. Minecraft est alors arrêté avant le
remplacement atomique de `ops.json`; l'original reste disponible jusqu'au
healthcheck réussi et est restauré automatiquement en cas d'échec.

## Développement

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest        # suite complète (~790 tests), aucun serveur requis
python -m ruff check app/ tests/
```

Domaine sans I/O dans `app/domain/`, adapters dans `app/adapters/`,
composition root dans `app/config.py`, API/UI FastAPI + Jinja dans
`app/api/`. Les tests utilisent des fakes en mémoire — la suite tourne
en quelques secondes. L'historique détaillé des décisions d'architecture
vit dans [CLAUDE.md](CLAUDE.md).

## Licence

[MIT](LICENSE) — © 2026 Jérémy Martinoty.
