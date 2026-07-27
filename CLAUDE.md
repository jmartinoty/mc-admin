# mc-admin — guide de développement

> Source de vérité commune à Claude Code et Codex. Codex y est redirigé par
> `AGENTS.md`. Fiche d'onboarding : architecture, conventions, workflow et
> **garde-fous sécurité**.

---

## 0. Workflow commun Claude Code / Codex

Ces règles évitent que deux agents modifient les mêmes fichiers, mélangent
leurs commits ou redéploient involontairement Minecraft.

### Isolation et collaboration

1. Commencer par lire `git status --short --branch`, le dernier commit de
   `main` et `git worktree list`.
2. Pour toute modification, créer une branche et un **worktree dédié** depuis
   le dernier `main` propre, par exemple :
   `git worktree add -b codex/<sujet> ../mc-admin-<sujet> main` (ou
   `claude/<sujet>` pour Claude).
3. Un worktree appartient à un seul agent. Ne jamais faire travailler Claude
   et Codex dans le même répertoire, même sur des fichiers a priori différents.
4. Ne jamais annuler, écraser, reformater globalement ou inclure dans un commit
   des changements non produits par l'agent courant. Une modification
   concurrente est présumée appartenir à Jeremy ou à l'autre agent.
5. Avant l'intégration, revérifier que `main` n'a pas avancé. Intégrer par
   fast-forward quand c'est possible ; sinon résoudre explicitement les
   conflits sans `reset --hard` ni `checkout --` destructif.
6. Faire un commit conventionnel par itération validée
   (`feat|fix|refactor|test|docs|chore: ...`). Ne pas laisser une fonctionnalité
   terminée uniquement dans le worktree.

### Validation avant commit

- Adapter les contrôles au changement : tests ciblés pendant le développement,
  puis `git diff --check`, `ruff check app tests` et la suite `pytest` complète
  avant tout déploiement de code.
- Pour une modification d'interface, vérifier réellement les parcours et les
  tailles desktop/mobile dans un navigateur, pas uniquement les templates.
- Pour une modification de persistance, valider le format des données réelles
  en lecture seule avant le déploiement et ne jamais afficher de secret.
- Si les dépendances de test ne sont pas disponibles sur le Mac, utiliser une
  image Docker temporaire avec un tag propre à l'itération, puis la supprimer.
- Une modification purement documentaire ne nécessite ni rebuild ni
  redéploiement de `mc-admin`.

### Git et informations privées

- `nas` (`NAS-ts:/Volume1/Projects/mc-admin`) est le remote privé utilisé pour
  livrer la production.
- `origin` est le remote GitHub **PUBLIC depuis le 19/07/2026** avec un
  historique DISTINCT : une entrée par release, fabriquée par squash de
  l'arbre courant (`git commit-tree main^{tree} -p <tête origin/main> -m …`
  puis push du commit + du tag `vX.Y.Z`). Ne JAMAIS pousser l'historique
  privé complet vers origin (de vieux commits contiennent des infos
  personnelles) et ne jamais y pousser sans demande explicite de Jeremy.
  L'historique de développement complet vit sur `nas` et en local.
- Ne jamais committer `.env`, secrets, données de production ou documentation
  personnelle. `docs/dev-depuis-mac.md` reste volontairement gitignoré.
- Avant de pousser, vérifier que le dépôt du TerraMaster est propre :
  `ssh NAS-ts git -C /Volume1/Projects/mc-admin status --short --branch`.
- Après intégration dans `main`, pousser normalement avec
  `git push nas main`. Ne pas pousser directement une branche de travail en
  production.

### Déploiement de production

**Depuis le 27/07/2026, l'installation du NAS est une install « end-user »** :
`mc-admin` et les one-shots (mc-restore, mc-op-levels, mc-doorman) tournent
sur l'image publiée `ghcr.io/jmartinoty/mc-admin:latest`. Plus aucun build
local, plus de `up -d --build`. **Livrer du code en production = publier une
release** :

1. intégrer et tester sur `main` (suite pytest complète + ruff), pousser `nas` ;
2. bump `APP_VERSION` (`app/config.py`) + entrée `CHANGELOG.md`, commit
   `chore(release): vX.Y.Z` ;
3. squash vers origin (workflow public, cf. « Git et informations privées »),
   tag `vX.Y.Z`, `gh release create` ;
4. la CI publie l'image ghcr (`latest` + semver) ; la carte MAJ de l'accueil
   la propose sous 24 h et **Jeremy applique depuis l'UI** (chemin nominal).

Application manuelle en secours (même séquence que le one-shot
`mc-admin-updater`) :

```bash
ssh NAS-ts "cd /Volume1/Projects/mc-admin \
  && docker compose --env-file .env pull mc-admin \
  && docker compose --env-file .env up -d --no-deps mc-admin \
  && docker compose --env-file .env --profile tools create --force-recreate \
       mc-restore mc-op-levels mc-backup-profile mc-doorman"
```

- Tester du code AVANT release : jamais sur les conteneurs de prod — suite
  pytest locale, et au besoin une image d'essai TAGUÉE construite sur le
  daemon NAS + conteneur jetable (pattern banc d'essai du 19/07/2026).
- Ne pas lancer un `docker compose up -d` global : il peut recréer les
  compagnons, provoquer des conflits de noms ou toucher au serveur de jeu.
- Ne jamais redémarrer `minecraft` pour une modification de `mc-admin`.
  N'élargir le déploiement à un autre service que si le changement le demande
  explicitement et après l'avoir annoncé à Jeremy.
- Après déploiement, vérifier `mc-admin` **et** `minecraft`, l'accès HTTPS et
  les logs récents :

```bash
ssh NAS-ts docker inspect \
  --format={{.Name}},status={{.State.Status}},health={{.State.Health.Status}},started={{.State.StartedAt}} \
  mc-admin minecraft
ssh NAS-ts "curl -kfsS --max-time 10 --resolve mc-admin.home:443:127.0.0.1 https://mc-admin.home/healthz"
ssh NAS-ts docker logs --since 10m --tail 200 mc-admin
```

Le démarrage de `minecraft` doit rester inchangé lors d'un déploiement
applicatif. Rendre compte du commit, des tests, du remote poussé et de l'état
des deux conteneurs.

### Décisions UX

Les propositions validées, leur périmètre et leurs critères d'acceptation sont
suivis dans [`docs/ux-roadmap.md`](docs/ux-roadmap.md). Mettre ce document à
jour lorsqu'un lot UX est commencé, terminé ou volontairement reporté.

### Chantier actif : installation Docker et onboarding

Jeremy a validé le chantier de distribution Docker destiné aux utilisateurs
finaux. Le plan détaillé et ses critères d'acceptation sont dans
[`docs/docker-onboarding-plan.md`](docs/docker-onboarding-plan.md). **Le lire
avant toute modification liée à l'installation, aux serveurs, à RCON, aux
workers ou à l'import joueurs.**

Décisions à ne pas contourner :

- travailler dans un worktree `claude/docker-onboarding`, jamais dans le
  répertoire de production ;
- installation V1 Docker Compose, un seul serveur actif ;
- retirer les `container_name` globaux et résoudre les workers par labels ;
- conserver le socket brut hors du web : helper one-shot à commande figée ;
- détecter RCON depuis `server.properties`, avec saisie manuelle en secours ;
- utiliser un volume mc-admin neuf et un Minecraft jetable pour le laboratoire ;
- ne jamais recopier, monter en écriture ou restaurer les données de production
  pendant ce chantier.

### Patch notes

Chaque correctif de bug (constaté par Jeremy ou en test réel) ajoute une
entrée courte dans [`docs/patch-notes.md`](docs/patch-notes.md) : date,
constat, cause/fix — que le correctif touche le code ou une config d'infra
adjacente (ex. BlueMap). Distinct de `CHANGELOG.md` (releases semver).

## 1. Vue d'ensemble

Service web **autonome et containerisé** pour administrer un serveur Minecraft
Fabric auto-hébergé (image `itzg/minecraft-server`, MC 26.1, conteneur
`minecraft`). Il est **embarquable** dans le dashboard maison `nas-dashboard`
(iframe / route derrière reverse-proxy `mc-admin.home`) mais reste **joignable
seul**.

**Objectif métier** : donner à un ami de confiance (rôle restreint) le droit de
voir l'état, redémarrer et sauvegarder le serveur — **sans SSH, sans pouvoirs
dangereux** (pas de RCON arbitraire, pas d'op, pas de stop définitif, pas
d'update).

**Stack** : Python 3.12 · FastAPI · uvicorn · Jinja2 + JS vanilla (polling) ·
docker-py (via socket-proxy) · RCON maison en stdlib (pas de lib mcrcon : elle
utilise SIGALRM, incompatible threadpool FastAPI) · argon2-cffi · PyYAML.
Pas de base de données (⚠️ exception scopée : SQLite pour l'historique des
joueurs, V3.4 §8 — rien d'autre n'en dépend), pas de SPA, pas de build front.

---

## 2. Architecture — hexagonale (ports & adapters)

Règle d'or : **le domaine (`app/domain/`) n'importe aucun I/O** — ni FastAPI, ni
docker-py, ni RCON, ni fichier. Il ne connaît que ses `Port`s (interfaces) et
son modèle. Les détails concrets vivent dans `app/adapters/` (pilotés) et
`app/api/` (pilotant / driving).

```
  HTTP (navigateur) ──▶ app/api  (FastAPI, auth, CSRF, RBAC de transport)
                           │  injecte
                           ▼
                     AdminService  (app/domain/services.py)
                     ├─ applique la RBAC CÔTÉ SERVEUR (deny-by-default)
                     ├─ journalise l'audit (qui/quoi/quand/résultat)
                     └─ orchestre les Ports :
                          GamePort · ContainerPort · BackupPort · LogPort · AuditPort
                                         │
                          app/adapters/*  (RCON, docker-socket-proxy, backup, logs, audit)
```

**Flux d'une action** : route → `current_user` (auth) → `AdminService.<action>(user, …)`
→ `_authorize(user, Permission.X)` (lève `PermissionDenied` sinon, refus audité)
→ appel Port(s) → audit succès/erreur → réponse. La sécurité vit dans le
**service**, pas dans les templates.

---

## 3. Structure du projet

```
app/
├── domain/            # cœur métier — ZÉRO I/O (testable sans Docker ni RCON)
│   ├── model.py       # Permission, Role, User, Player, ContainerState,
│   │                  #   ServerStatus, BackupResult, AuditEntry
│   ├── errors.py      # PermissionDenied, ServerUnavailable, ConfigError…
│   ├── ports.py       # interfaces (Protocol) : Game/Container/Backup/Log/Audit/Clock
│   ├── rbac.py        # build_roles/build_users : YAML parsé -> Role/User (pur)
│   └── services/      # AdminService = façade (__init__.py) assemblée de mixins
│                      #   par thème (V7.0) : base (ServiceCore : RBAC + audit),
│                      #   monitoring, servers, accounts, actions, backups,
│                      #   game, update, moderation — import public inchangé
├── adapters/          # implémentations concrètes des ports  (à venir, incrémental)
├── api/               # FastAPI : app, auth, CSRF, routes, templates/ static/  (à venir)
└── config.py          # lecture env / chargement RBAC au démarrage            (à venir)
config/roles.example.yml   # gabarit rôles + utilisateurs (le vrai roles.yml est gitignoré)
tests/                # un test_<module>.py par module de domaine ; fakes.py partagé
```

**Ordre de construction (incrémental, pas de big-bang)** :
`domain/` + tests ✅ → adapters (RCON, proxy, logs, audit) → api (auth/CSRF/routes) → UI.

---

## 4. Modèle RBAC (permissions, pas de rôles en dur)

Le domaine ne connaît que des **`Permission`** (énum = contrat de sécurité). Un
**rôle est un simple regroupement de permissions défini en YAML** — aucun nom de
rôle (`owner`/`admin`/`friend`) n'est codé en dur. Ajouter/modifier un rôle =
éditer le YAML, pas le code.

```yaml
# config/roles.yml  (monté en lecture seule, gitignoré)
roles:
  owner:  ["*"]                                   # "*" = wildcard = toutes les permissions
  admin:  [STATUS, LOGS_VIEW, RESTART, BACKUP_TRIGGER]
  friend: [STATUS, LOGS_VIEW]
users:
  jeremy: { role: owner }
  paul:   { role: admin }
```

Permissions disponibles (`Permission`) : `STATUS`, `LOGS_VIEW`, `RESTART`,
`START`, `BACKUP_TRIGGER`, `RCON_RAW`, `OP_MANAGE`, `STOP`, `UPDATE`,
`WHITELIST_MANAGE`.

- **deny-by-default** : toute permission non listée est refusée.
- `RESTART` ≠ `STOP` : le restart passe par `ContainerPort.restart()`. `STOP`
  (arrêt définitif), `UPDATE`, `RCON_RAW`, `OP_MANAGE` restent réservés owner.
- La config est **chargée au démarrage puis gardée en mémoire** (pas de relecture
  par requête). Un changement de rôles nécessite un redémarrage du conteneur.

---

## 5. Commandes

```bash
# Tests — le domaine se teste SANS Docker ni RCON (fakes des ports)
pytest                                   # tous les tests (via pytest.ini : pythonpath=app .)
pytest tests/test_services.py            # un module
python -m unittest discover -s tests -t . # équivalent stdlib (aucune dépendance à installer)

# Lint
ruff check app/                          # E, F, W — ligne 120
ruff check --fix app/

# Déploiement : l'install NAS consomme l'image ghcr publiée par la CI —
# livrer = publier une release (workflow complet et contrôles : §0).
ssh NAS-ts docker logs -f mc-admin
```

Le code est **intégré à l'image** (pas de bind-mount) → toute modif
code/template n'atteint la prod que par une release (image ghcr), cf. §0.

---

## 6. Conventions de code

- **Domaine pur** : `app/domain/` n'importe JAMAIS `fastapi`, `docker`, `mcrcon`,
  `yaml`, `open()`. Si un import I/O apparaît là, c'est un bug d'architecture.
- **Type hints** obligatoires sur toute fonction publique. `from __future__ import
  annotations` en tête (compat 3.10 → 3.12).
- **Tests** : toute fonction publique du domaine est testée avec des **fakes de
  ports** (voir `tests/fakes.py`), jamais de Docker/RCON réels. Couvrir : nominal,
  refus RBAC, erreur d'un port, dégradation (serveur injoignable).
- **RBAC & audit dans le service**, jamais seulement dans l'UI.
- **Secrets** : jamais en dur, jamais commités. Tout passe par env / fichiers
  montés. Toute nouvelle variable → ajoutée à `.env.example`.
- **Lint** : `ruff.toml` (py312, ligne 120, `E/F/W`, ignore `E402/E501`).
- **Commits conventionnels** : `feat|fix|refactor|test|chore: …`.
- **Copy UI en langage utilisateur** (retour Jeremy, 08/07/2026) : jamais de
  chemins internes (`/backups/scheduled`), de jargon (« dry-run », « persisté »)
  ni de détails d'implémentation dans l'interface. Dire ce que ça fait pour
  l'utilisateur, pas comment c'est fait. Les détails techniques vivent dans
  CLAUDE.md et les docstrings.

---

## 7. Sécurité & garde-fous (NON négociables — justifiés dans le code)

1. **Jamais `/var/run/docker.sock` dans l'app.** L'app parle à un
   **`docker-socket-proxy`** (`tecnativa/docker-socket-proxy`) via
   `DOCKER_HOST=tcp://mc-socket-proxy:2375`, avec une whitelist **minimale** :
   `CONTAINERS=1` + `ALLOW_START/ALLOW_STOP/ALLOW_RESTARTS=1`. Tout le reste
   (`EXEC`, `IMAGES`, `VOLUMES`, `NETWORKS`, `INFO`, `AUTH`…) = **0**.
   - ⚠️ **Risque résiduel documenté** : le proxy filtre par **catégorie d'API
     Docker**, **pas par nom de conteneur**. Donc `POST /containers/{id}/restart`
     resterait techniquement possible sur *n'importe quel* conteneur.
     **Mitigation** : l'app est configurée avec `MINECRAFT_CONTAINER` et son
     `ContainerPort` **refuse explicitement toute cible ≠ ce conteneur**
     (`UnauthorizedContainer`). Le proxy est la 2ᵉ barrière (pas d'exec, pas
     d'images). `UPDATE`/recreate (V2) élargirait la surface → adapter dédié à
     évaluer, pas d'élargissement aveugle du proxy.
2. **RCON jamais publié.** Le port `25575` reste **interne** au réseau Docker
   (`mc_playit`), non mappé sur l'hôte. `RCON_PASSWORD` vient de l'env/secret.
   → Le conteneur `minecraft` doit définir `RCON_PASSWORD` (voir README §Intégration).
3. **RBAC côté serveur** sur chaque action (§4), deny-by-default. L'UI masque les
   boutons pour l'UX, mais ce n'est **jamais** la barrière de sécurité.
4. **Audit** (`AuditPort`) : append-only. On journalise **toute action mutante**
   (restart/backup/stop/rcon…) en succès ET erreur, **et tout refus RBAC** (y
   compris sur les lectures). Les lectures réussies (status/logs) ne sont pas
   journalisées pour éviter d'inonder le journal sous le polling — décision
   assumée, inverser si besoin dans `AdminService`.
5. **Auth** : sessions cookie signées (`SESSION_SECRET`), mots de passe hachés
   **Argon2**. Cookies `HttpOnly` + `Secure` + `SameSite=Lax`. **CSRF sur tous
   les POST** (token de formulaire). Pas d'accès public — LAN/Tailscale/reverse-proxy.
   ✅ **TLS en place depuis le 03/07/2026** (NPM + certificat wildcard mkcert
   `*.home`, cf. §9) : `SESSION_COOKIE_SECURE=true` en prod. Ne repasser à
   `false` que pour un test ponctuel en HTTP nu (le cookie ne serait alors plus
   envoyé par le navigateur en HTTPS).
6. **`.env` gitignoré**, `.env.example` fourni (valeurs vides). `config/roles.yml`
   gitignoré ; seul `config/roles.example.yml` est versionné.
7. **États JSON persistants** : utiliser exclusivement les primitives de
   `app/adapters/atomic_json.py` pour tout nouveau store modifiable :
   `shared_path_lock()` autour du cycle lecture-modification-écriture,
   `load_json(strict=True)` avant une mutation et `atomic_write_json()` pour
   remplacer le fichier (temp unique + `fsync` + `os.replace`). Les lectures
   d'affichage restent tolérantes, mais une mutation ne doit **jamais** écraser
   silencieusement un fichier existant illisible ou incohérent
   (`CorruptJsonError`). Les fichiers contenant des secrets
   (`passwords.json`, `notifications.json`) restent en `0600`.

---

## 8. Décisions actées

- **Backup** : ✅ implémenté. Conteneur `itzg/mc-backup` dédié en one-shot
  (`BACKUP_INTERVAL=0` → une passe puis exit), (re)démarré à la demande via le
  proxy (`POST /containers/mc-backup/start`). `BackupPort` reste ignorant de
  Docker ; l'adapter `DockerBackupTrigger` porte le garde-fou de nom dédié.
  Archives → `/Volume1/Backups/minecraft`. Pas de `docker exec`.
- **Logs** : lus depuis le **fichier `latest.log` monté en lecture seule**
  (`MC_LOG_FILE`), pas via `docker logs`, pas via RCON.

### Roadmap V2 (validée — ordre : whitelist → métriques → update)

- **V2.1 Whitelist (owner)** : ✅ implémenté. `whitelist/whitelist_add/remove`
  dans `AdminService` (barrière `WHITELIST_MANAGE`, mutations auditées avec le
  pseudo, pseudo validé `[A-Za-z0-9_]{3,16}` AVANT tout envoi RCON — anti
  injection). Panneau UI owner-only, hors fragment pollé (sinon la saisie
  serait écrasée par le swap).
- **V2.2 Métriques Prometheus** : ✅ implémenté. Stack `AppData/monitoring`
  (copie versionnée : `deploy/monitoring/`) : Prometheus (rétention 30 j) +
  cAdvisor (**épinglé v0.49** : >= 0.50 a retiré la factory Docker, plus de
  label `name=`) + node_exporter + mc-monitor (joueurs/latence par ping, sans
  mod ; TPS = mod Fabric à ajouter un jour → simple entrée de config).
  Côté app : `MetricsPort` (lecture seule, barrière STATUS), adapter
  `PrometheusMetrics` (urllib stdlib), requêtes **PromQL prédéfinies dans
  `config/metrics.yml`** (versionné, non sensible) — jamais de PromQL depuis
  l'UI. Une métrique = une entrée YAML (key/label/unit/query), extensible NAS &
  autres services. Prometheus injoignable → panneau « indisponible », requête
  individuelle en erreur → tiret sur cette carte seulement.
- **V2.3 Update contrôlé (owner)** : ✅ implémenté. Conteneur **`mc-updater`**
  one-shot (image `docker:27-cli`, commande FIGÉE `compose pull && up -d` du
  seul service minecraft) — c'est LUI qui monte le socket Docker, jamais
  mc-admin, dont la whitelist proxy est INCHANGÉE. Vérification de version sans
  élargir le proxy : version courante = label `server_version` de mc-monitor
  (via Prometheus), dernière release = manifeste Mojang (piston-meta, cache
  60 s), changelog = minecraft.wiki. Garde-fous appliqués dans le service :
  refus si joueurs connectés OU nombre inconnu (RCON down) sauf `force` ;
  `say` + `save-all` avant l'arrêt (best-effort tracé) ; chaque issue auditée
  avec le détail des étapes. ⚠️ `mc-backup`/`mc-updater` sont sous le profil
  compose `tools` : jamais lancés par `up -d` (sinon backup/update à chaque
  déploiement !) — création one-time :
  `docker compose --profile tools create mc-backup mc-backup-scheduled mc-updater`.
  Changer de version MC = éditer `VERSION` dans le compose du serveur puis
  lancer la mise à jour (le `up -d` de l'updater applique l'édition).

### Roadmap V3 (en cours — sidebar → OP → notifications → sauvegardes
### programmées → historique joueurs → TPS)

- **V3.1 Sidebar + Opérateurs (owner)** : ✅ implémenté. Nouveau shell HTML
  (`templates/shell.html`) : sidebar persistante (Status/Whitelist/Opérateurs/
  Journal), remplace les topbars dupliquées par page. Whitelist déplacée de la
  page Status vers sa propre page `/whitelist` (même pattern que la nouvelle
  page `/op`) — la page Status reste focalisée sur l'état + actions rapides.
  Gestion des opérateurs (`Permission.OP_MANAGE`, déjà présente dans le modèle
  mais jamais câblée) : **lecture** via `OpsPort`/`OpsFileReader` (fichier
  `ops.json` monté en LECTURE SEULE — aucune commande RCON « op list » n'existe
  côté vanilla/Fabric), **mutation** via RCON (`GamePort.op/deop`, qui met à
  jour `ops.json` côté serveur). Confirmation renforcée à l'ajout (pas
  seulement au retrait) : un op contourne la plupart des protections in-game.
- **V3.2 Notifications** (Discord + Telegram) : ✅ implémenté.
  `NotificationPort` (contrat : `notify()` NE LÈVE JAMAIS — un canal en panne
  ne doit jamais casser une action). `CompositeNotifier` combine 0..N canaux,
  isole chaque envoi (Discord down n'empêche pas Telegram) ; liste vide = no-op
  silencieux, même politique que `NotConfiguredBackup`. Canaux activés
  individuellement selon les variables d'env présentes (`DISCORD_WEBHOOK_URL`,
  `TELEGRAM_BOT_TOKEN`+`TELEGRAM_CHAT_ID`).
  **Portée volontairement bornée à ce qui est fiable AUJOURD'HUI** (pas de
  nouvelle infra de fond) : notifie l'échec de restart/start/stop/backup/update,
  et le succès d'une mise à jour appliquée.
  *(Mise à jour V5.x : « joueur rejoint/part » et « serveur down » sont
  désormais implémentés — greffés sur le tailer de la V3.4 et sur
  `app/health_watcher.py` — mais DÉSACTIVÉS par défaut, cf. V5.x.)*
  Jamais notifié : un simple refus RBAC (bruit, pas anomalie).
- **V3.3 Sauvegardes programmées** : ✅ implémenté. `BackupScheduler`
  (`app/scheduler.py`, thread de fond stdlib, `threading.Event.wait`
  interruptible) rejoue **exactement** `AdminService.trigger_backup` — pas de
  logique dupliquée — sous un `SYSTEM_USER` construit en code (rôle
  `automation`, une seule permission `BACKUP_TRIGGER`, n'apparaît jamais dans
  `config/roles.yml`, pas un compte connectable). Conséquence : audit + les
  notifications d'échec de la V3.2 s'appliquent GRATUITEMENT aux sauvegardes
  programmées (`username="scheduler"` dans le journal). Démarré/arrêté via le
  `lifespan` FastAPI (`app/api/app.py`). `BACKUP_SCHEDULE_HOURS<=0` (défaut) =
  désactivé, comportement V1 inchangé. Pas de cron complet (overkill pour ce
  projet) : intervalle fixe en heures depuis le démarrage du conteneur.
- **V3.4 Historique des joueurs** : ✅ implémenté. **Exception ASSUMÉE** au
  choix « pas de base de données » de la V1 (§1) : `SqlitePlayerHistory`
  (`app/adapters/player_history.py`), scopée STRICTEMENT à ce feature — rôles/
  config restent YAML, audit reste JSONL, rien d'autre ne migre vers une DB.
  Justification : agrégation (somme de durées, tri par dernière connexion)
  qu'un fichier plat rendrait pénible.
  - `PlayerLogWatcher` (`app/player_watcher.py`) : tail de `latest.log` en
    tâche de fond (thread stdlib, poll ~2 s), détecte « X joined/left the
    game ». Seek en FIN de fichier au démarrage : ne capture que les
    événements FUTURS (le fichier peut contenir des mois d'historique).
  - Écrit DIRECTEMENT dans `PlayerHistoryPort` — **PAS via AdminService** :
    contrairement au scheduler de backup (V3.3), ce n'est pas une action
    utilisateur soumise à RBAC, c'est de l'observation passive (comme un
    collector). Seule la LECTURE (`AdminService.player_history`, barrière
    `STATUS`, non auditée en succès) passe par le service.
  - Deux instances de `SqlitePlayerHistory` pointent le même fichier (une pour
    le service qui lit, une pour le watcher qui écrit) : sans risque, l'objet
    ne garde aucune connexion ouverte entre les appels (`config.build_player_history`).
  - Page `/players` visible à quiconque a `STATUS` (donc quasi tout le monde) :
    pseudo, statut en ligne/hors ligne, temps de jeu cumulé, dernière connexion.
- **V3.5 TPS réel** : ✅ implémenté. **Zéro code côté mc-admin** — uniquement
  de la config, exactement comme prévu par le design modulaire de la V2.2.
  - Mod **FabricExporter** (`mods/fabricexporter-26.1-1.0.21.jar`, téléchargé
    depuis Modrinth, SHA1 vérifié) ajouté côté serveur minecraft — utilise
    **spark** (déjà installé) pour lire TPS/MSPT, expose son propre endpoint
    Prometheus natif (port 25585). Dépendances : Fabric API (déjà présente),
    spark (optionnelle mais déjà présente).
  - ⚠️ **Piège RCON écarté après vérification** : interroger `/spark tps` via
    RCON est documenté comme non fiable (issue GitHub non résolue, réponse
    vide) — et `overrideTpsCommand` de spark est spécifique à Bukkit/Paper,
    ne s'applique pas à Fabric. FabricExporter contourne le problème : il lit
    Spark en interne (même JVM), pas par commande RCON.
  - Prometheus (`AppData/monitoring`) rejoint le réseau `mc_playit` pour
    scraper `minecraft:25585` directement (nouveau job `minecraft_exporter`),
    en plus de son scrape existant de `mc-monitor` (joueurs/latence par ping).
  - `config/metrics.yml` de mc-admin : 2 entrées ajoutées (`tps`, `mspt` —
    `minecraft_mspt{type="mean"}`), zéro modification Python.
  - ⚠️ **Piège de permissions rencontré** : un fichier téléchargé depuis le Mac
    (via SMB) appartient à l'utilisateur du Mac mappé côté NAS, PAS à l'uid
    1000 attendu par le conteneur (constaté par comparaison avec les autres
    mods) → `FileNotFoundError: Permission denied` au chargement Fabric,
    corrigé par un `chown 1000:1000` côté NAS après téléchargement. À refaire
    pour tout futur ajout de mod depuis le poste de dev.
  - ⚠️ Même piège de chemin relatif que pour mc-admin (§9) rencontré sur le
    compose de la stack `monitoring` (jamais corrigé depuis la migration Mac,
    car jamais redéployé depuis) : `./prometheus/prometheus.yml` → chemin
    absolu `/Volume1/AppData/monitoring/prometheus/prometheus.yml`.

### Roadmap V4 (structuration UI + features inspirées des outils du marché)

- **V4.1 Sidebar regroupée** : ✅ implémenté. Section "Joueurs" (libellé de
  groupe non cliquable, `.nav-group-label`) regroupant Historique (`/players`,
  ex-« Joueurs »)/Whitelist/Admin (`/op`, ex-« Opérateurs ») en sous-items
  (`.navitem.sub`). Le groupe n'apparaît que si l'utilisateur a au moins une
  des trois permissions (`can_players or can_whitelist or can_op`). URLs
  inchangées (`/op` reste `/op`), seuls les libellés bougent.
- **V4.2 Page Sauvegardes** : ✅ implémenté. `BackupArchivesPort` (nouveau,
  distinct de `BackupPort` — même logique que OpsPort/GamePort : lister est un
  concern différent de déclencher) + adapter `FileBackupArchives`, lecture
  seule d'un nouveau mount `/Volume1/Backups/minecraft:/backups:ro`. Filtre les
  fichiers non-`.tar.gz` (ex. `.mc-backup-lock` de mc-backup). Le bouton
  « Sauvegarder » déménage de Status vers `/backups` (même barrière
  `BACKUP_TRIGGER`) ; Status reste focalisé sur état + actions rapides.
- **V4.3 Console RCON live** (owner) : ✅ implémenté. Page `/console` : champ
  de commande unique, exécutée via `AdminService.run_rcon` (déjà existant),
  confirmation JS avant envoi (aucune restriction de commande — c'est
  l'échappatoire complète, volontairement réservée à `RCON_RAW`). Nouvelle
  méthode `AdminService.can_access_console` : barrière RBAC dédiée pour le GET
  (pas de donnée à renvoyer, juste garantir que le refus soit audité comme
  partout ailleurs — la RBAC ne vit jamais directement dans la route).
  Résultat affiché en `<pre>`, lien direct vers `/audit` (chaque commande y
  est déjà journalisée par `run_rcon`).
- **V4.4a Kick + ban permanent** : ✅ implémenté. Nouvelles permissions dédiées
  `Permission.KICK` (owner+admin, moins sensible — n'affecte pas les futures
  connexions) et `Permission.BAN_MANAGE` (owner, plus sévère). `GamePort`
  étendu (`kick/ban/pardon`), lecture via `BansPort`/`FileBans` (fichier
  `banned-players.json` monté RO, même politique que `ops.json` — `/banlist`
  existe bien via RCON mais son parsing serait fragile). Raison (texte libre)
  nettoyée avant tout envoi RCON (`_sanitize_reason` : pas de retour à la
  ligne, longueur bornée) — défense en profondeur distincte de
  `_validate_player_name` (qui, elle, s'applique aux pseudos). Bouton kick
  intégré à la page Status (à côté de chaque joueur en ligne) ; ban/pardon
  sur une nouvelle page `/bans` (groupe sidebar "Joueurs").
  Vérifié en réel : `/banlist` confirmé fonctionnel côté vanilla (contrairement
  à `/spark tps`), format de `banned-players.json` confirmé avant
  implémentation ; commandes kick/ban/pardon testées contre le vrai RCON.
- **V4.4b Ban temporisé** : ✅ implémenté. Vanilla n'a AUCUNE durée native sur
  `/ban` (confirmé) : `AdminService.ban_temporary(user, player, hours, reason)`
  pose donc un ban **permanent** classique puis programme sa propre levée via
  `TempBanPort`/`JsonTempBans` (fichier `/data/pending_unbans.json`, même
  volume que le reste de l'état applicatif — pas de nouveau montage). Le champ
  `hours` du formulaire `/actions/ban` est optionnel : vide = ban permanent
  (comportement V4.4a inchangé), `> 0` = temporisé.
  - `TempBanScheduler` (`app/tempban_scheduler.py`) : même squelette que
    `BackupScheduler` (thread stdlib, `threading.Event.wait` interruptible,
    `sleep`/`clock` injectables pour les tests) sous un utilisateur système
    dédié `AUTO_UNBAN_USER` (rôle `automation`, seule permission
    `BAN_MANAGE`, non connectable) — même famille que `SYSTEM_USER` (V3.3)
    mais un utilisateur distinct pour que l'audit distingue clairement
    sauvegarde programmée et levée automatique de ban (`username="auto-unban"`).
    Poll configurable (`TEMP_BAN_POLL_SECONDS`, défaut 60 s).
  - Garde-fous de cohérence : `ban()` (permanent) et `pardon()` (manuel)
    annulent tous deux toute levée programmée existante pour ce joueur
    (`_temp_bans.cancel`) — un ban permanent ne doit jamais être levé plus
    tard par un minuteur obsolète, et une levée manuelle rend inutile toute
    levée automatique encore programmée. Reprogrammer un ban déjà temporisé
    remplace le minuteur existant (pas de doublon).
  - Page `/bans` : colonne "Durée" annotée ("temporaire — expire le …" vs
    "permanent") à partir de `AdminService.pending_unbans(user)`.
  - Vérifié en réel : une entrée expirée injectée directement dans
    `/data/pending_unbans.json` a bien été détectée au poll suivant et levée
    via un vrai `pardon` RCON, journalisée dans l'audit sous `auto-unban`.
- **V4.5 Redémarrage programmé avec avertissement** : ✅ implémenté. Généralise
  le `say`+`save-all` déjà écrit pour l'update (V2.3) : au lieu d'un
  avertissement unique juste avant l'arrêt, une série d'avertissements
  dégressifs jusqu'au redémarrage effectif. `AdminService.schedule_restart`
  (barrière `RESTART`, réutilise la permission existante — pas de nouvelle
  permission) programme l'échéance (état en mémoire, `RestartSchedulerPort`/
  `InMemoryRestartSchedule`, **volontairement non persistant** — contrairement
  au ban temporisé, perdre une programmation si mc-admin redémarre est un
  compromis acceptable pour une action ponctuelle de courte portée) et diffuse
  immédiatement un `say` de confirmation.
  - Toute la logique de seuils (`AdminService.tick_scheduled_restart`, appelée
    en boucle par `RestartWarningScheduler`, `app/restart_scheduler.py`) vit
    dans le SERVICE, pas dans le thread — même discipline que
    `BackupScheduler`/`TempBanScheduler` : le thread de fond ne connaît QUE
    `AdminService`, jamais directement `GamePort`. Seuils d'avertissement :
    10/5/1 min puis 30/10 s avant l'échéance ; un seuil déjà dépassé AU MOMENT
    DE LA PROGRAMMATION (ex. "10 min" pour un délai programmé de 4 min) n'est
    jamais annoncé rétroactivement (calculé une fois pour toutes dans
    `InMemoryRestartSchedule.schedule`, pas déduit après coup du timing du
    thread de fond).
  - À échéance : `say` final + `save-all` (best-effort) puis rappel de
    `restart()` — même chemin audité que le bouton manuel — sous un
    utilisateur système dédié `SCHEDULED_RESTART_USER` (rôle `automation`,
    seule permission `RESTART`), même famille que `SYSTEM_USER`/
    `AUTO_UNBAN_USER` mais distinct pour que l'audit distingue clairement
    redémarrage programmé et redémarrage manuel.
  - Page Status : panneau dédié (formulaire "programmer" si rien en attente,
    sinon countdown + bouton "annuler"), dans le fragment déjà pollé toutes
    les 5 s (pas de JS supplémentaire nécessaire).
- **V4.6 Niveaux d'OP** : ✅ implémenté et durci. Aucune commande Java
  Edition ne change le niveau individuel à la volée : `/op` et `/deop`
  restent immédiats via RCON, tandis qu'un niveau différent est enregistré
  dans `pending_op_levels.json` pour le prochain redémarrage demandé.
  - `ops.json` est monté en **LECTURE SEULE** dans mc-admin. Minecraft reste
    son unique écrivain pendant qu'il tourne.
  - Le conteneur one-shot `mc-op-levels` réclame atomiquement les niveaux en
    attente, arrête Minecraft, remplace `ops.json` depuis le dossier de données
    complet, puis attend le healthcheck.
  - Le mode, l'UID, le GID et les champs inconnus de chaque entrée sont
    préservés. Un rollback local reste présent jusqu'au retour healthy ; en cas
    d'échec, l'original est restauré et les niveaux retournent dans la file.
  - Le fichier `applying` et un verrou inter-processus empêchent de perdre une
    demande ajoutée pendant une transaction ou après une interruption.
  - `AdminService.op_add` réutilise la permission `OP_MANAGE` existante (pas
    de nouvelle permission) et sert À LA FOIS l'ajout d'un nouvel op et le
    changement de niveau d'un op existant (`/op` RCON est idempotent sur un
    joueur déjà opérateur).
  - Page Admin (`/op`) : sélecteur de niveau (1 à 4, avec description courte)
    sur le formulaire d'ajout ET sur chaque op existant (mini-formulaire
    "changer"), plus un avertissement permanent rappelant la contrainte du
    redémarrage.

### Roadmap V5 (✅ livrée — refonte UI + backups avancés + self-service)

> Développée en parallèle par plusieurs agents (Claude Code + Codex) sur le
> même arbre — d'où des commits croisés. Tout est fusionné et testé.

- **V5 UI — refonte complète** : ✅. Design system en tokens CSS
  (`static/style.css`) : palette vert-herbe dérivée du logo (`favicon.svg`),
  thèmes sombre/clair (`prefers-color-scheme` + `data-theme` persisté en
  localStorage, préchargé dans `<head>` anti-flash), typo mono pour
  marque/données/terminal. Login signé (logo + titre machine à écrire).
  Tuile dashboard fusionnée (status + version + actions play/restart/stop en
  icônes + redémarrage programmé en sous-panneau). **Terminal continu** :
  logs + console RCON en une surface (logs dans le fragment pollé,
  prompt/résultat statiques dockés dessous — frontière du polling respectée),
  coloration par niveau (WARN/ERROR/join), focus `/`, Échap, historique ↑↓
  (localStorage). **Page Joueurs unique** : whitelist/op/bans fusionnés
  (routes `/whitelist` `/op` `/bans` conservées par URL, plus dans la
  sidebar), filtres client par data-attributs, chips d'état, actions par
  ligne en boutons-icônes ouvrant une **`<dialog>` de confirmation commune**
  (raison/durée saisies dans la modale — plus aucun `confirm()`/`prompt()`
  natif sur cette page), formulaires « préventifs » (whitelist/ban/op d'un
  pseudo jamais vu) affichés selon le filtre actif.
- **V5 Backups avancés** (permissions dédiées, majoritairement Codex) :
  - `BACKUP_DOWNLOAD` : téléchargement d'une archive depuis `/backups`
    (`archive_path` : validation anti-traversal, résolution canonique).
  - Sauvegardes **manuelles vs programmées séparées** : deux conteneurs
    one-shot (`mc-backup` → `/backups/manual`, `mc-backup-scheduled` →
    `/backups/scheduled`), le scheduler utilise le second ; la rétention ne
    cible QUE `scheduled/`.
  - `BACKUP_RETENTION` : prévisualisation de rétention (journaliers N jours +
    mensuels M mois) en dry-run sur la page `/backups`.
  - `BACKUP_RESTORE` (owner) : restauration d'une archive via conteneur
    **`mc-restore`** one-shot privilégié (même modèle que mc-updater : socket
    Docker monté chez LUI, jamais mc-admin ; cible lue dans un fichier écrit
    par mc-admin et revalidée dans le worker). `restore_backup` exige une
    **sauvegarde de sécurité vérifiée du monde actuel avant l'écrasement**.
    Le worker prépare l'archive dans un staging avant l'arrêt, clone le monde
    actif dans un rollback local Btrfs (reflink, copie en repli), synchronise
    le contenu en place puis redémarre Minecraft. Le rollback reste présent
    jusqu'au healthcheck Docker `healthy` (300 s par défaut). Une erreur
    d'application, de démarrage ou de santé restaure automatiquement le
    rollback. Un marqueur JSON permet aussi de reprendre prudemment une
    transaction interrompue.
    ✅ **Testé en réel le 12/07/2026** (protocole : sauvegarde fraîche →
    marqueur `random_tick_speed` 3→5 → restauration → marqueur revenu à 3,
    seed intact, serveur healthy). La réserve constatée ce jour-là (sauvegarde
    de sécurité concurrente de l'arrêt/extraction → archive potentiellement
    corrompue) est **corrigée** : la restauration est désormais SÉQUENCÉE —
    `restore_backup` pose un état « en attente » (`PendingRestorePort`, en
    mémoire, non persistant : mode d'échec le plus sûr, le monde n'est pas
    touché) et `RestoreCoordinator` (thread de fond, même famille que les
    schedulers) ne démarre mc-restore que lorsque `BackupPort.is_running()`
    passe à faux ; deadline 15 min sinon abandon audité+notifié. Validé en
    réel : sauvegarde de sécurité terminée proprement (zéro erreur RCON,
    contre 7 la veille en parallèle) avant le démarrage de mc-restore.
    Création : `docker compose --profile tools create mc-restore`. Après une
    mise à jour de l'image :
    `docker compose --profile tools create --force-recreate mc-restore`.
- **V5 Menu profil (self-service, hors RBAC)** : popover sur l'utilisateur en
  bas de sidebar. **Nom affiché** (cosmétique, `/data/display_names.json` —
  le pseudo reste la clé RBAC + l'identité d'audit) ; **changement de mot de
  passe** : `config/roles.yml` reste RO, le nouveau hash Argon2 va dans un
  **overlay inscriptible `/data/passwords.json`** consulté EN PRIORITÉ au
  login (le hash de roles.yml reste nécessaire pour la première connexion) ;
  bascule de thème ; déconnexion.
- ⚠️ **Contrainte process unique** : les fichiers JSON de `/data` utilisent
  désormais des écritures atomiques durables et un verrou partagé entre toutes
  les instances d'adapter visant le même chemin **dans un processus**. Cela
  protège les threads et évite les mises à jour perdues dans l'uvicorn actuel,
  mais ce verrou n'est pas inter-processus. L'état en mémoire (redémarrage
  programmé) impose également **un seul worker uvicorn**. Ne jamais passer
  `--workers N>1` sans introduire un verrou inter-processus ou un stockage
  transactionnel et déplacer les états mémoire concernés.

### Roadmap V6 (en cours — « produit » : déployable chez n'importe qui)

Cap validé par Jeremy (16/07/2026) : rendre mc-admin déployable facilement
(publication à terme) + intégration nas-dashboard. Étape par étape, en
commençant par les sauvegardes ; « ajouter un serveur » viendra ensuite.

- **V6.1 Profils de sauvegarde** : ✅ implémenté. Un profil = quoi
  (catégories FERMÉES : world/mods/mods_jars/config/admin/all, traduites en
  INCLUDES/EXCLUDES tar par `adapters/backup_profiles.py` — le nom du monde
  vient de `MC_WORLD_DIR`), où (dest DÉRIVÉ `profiles/<slug>`, jamais un
  chemin libre), quand (manuel / toutes les X h / quotidien HH:MM, échéances
  V5 conservées avec rattrapage), rétention par profil (0 = illimitée ;
  suppression physique possible UNIQUEMENT sous scheduled/ et profiles/ —
  manual/ et restore-safety/ restent hors de portée de mc-admin).
  - **Conteneur générique `mc-backup-profile`** piloté par CONSIGNE
    (`data/backup-profile.env`, sourcée par l'entrypoint au démarrage —
    pattern mc-restore, zéro droit Docker ajouté). Création one-time :
    `docker compose --profile tools create mc-backup-profile`, et le dossier
    hôte `/Volume1/Backups/minecraft/profiles` doit exister pour le mount
    imbriqué RW de mc-admin.
  - **Permission `BACKUP_MANAGE`** (owner) pour le CRUD ; BACKUP_TRIGGER
    suffit pour déclencher. UI : cartes par profil + état vide « + Créer une
    configuration » + modale en 3 étapes (quoi/quand/rétention) ; les
    dialogues destructifs restent intégrés (règle : pas de confirm() natif).
  - **Migration V5→V6** : au premier démarrage, `seed_from_legacy` crée les
    profils « Manuelles » (dest manual/, sans rétention) et « Automatiques »
    (dest scheduled/, absorbe la planification de backup_schedule.json).
    Les anciens conteneurs mc-backup/mc-backup-scheduled deviennent inutiles
    (gardés en compose pour retour arrière) ; mc-backup-safety INCHANGÉ.
  - Les chemins hérités (trigger_backup, tick, progress) basculent
    automatiquement sur les profils quand ils sont configurés — les tests
    couvrent les deux modes.

- **V6.2 Premier lancement** : ✅ implémenté. `roles.yml` ABSENT = rôles par
  défaut (`DEFAULT_ROLES_SPEC`, domaine pur) et zéro compte → tout redirige
  vers `/setup` (création du compte administrateur : hash Argon2 →
  `/data/passwords.json`, rôle → `/data/users.json`, connexion directe).
  Verrouillé dès qu'UN compte existe. Le tableau de bord se DÉGRADE
  proprement (« Serveur injoignable ») quand conteneur/proxy manquent —
  une installation à moitié branchée guide au lieu de planter en 500.
  Testé en réel sur instance jetable (docker run + /data vide).
- **V6.3 Serveurs & branchements** : ✅ implémenté. Entité `ServerEntry`
  (`/data/servers.json`, migration douce : la config env devient le
  « Serveur principal » au premier démarrage, et le PREMIER serveur du
  fichier prime sur l'env à la construction des adapters — le mot de passe
  RCON reste env, jamais dans le fichier). Page `/servers` (owner,
  `Permission.SERVER_MANAGE`) : branchements testés EN DIRECT (conteneur/
  proxy, RCON, logs, volume sauvegardes, outil de sauvegarde, Prometheus)
  avec quoi-corriger en langage utilisateur ; DÉTECTION des conteneurs
  Minecraft (`DockerServerDiscovery`, listage déjà autorisé par le proxy —
  image *minecraft-server* ou port 25565) proposés à l'ajout préremplis ;
  formulaire manuel. Le premier serveur ajouté sur une install fraîche
  prend effet après redémarrage de mc-admin (adapters construits au
  démarrage) ; les entrées suivantes attendent la V7 (multi-serveur +
  mode « hôte » sans Docker — les ports rendent ça possible sans toucher
  au domaine). Après /setup, l'utilisateur atterrit sur /servers.
- **V6.4 Comptes + finitions** : ✅ implémenté. Page `/users` (owner,
  `Permission.USER_MANAGE`) : création (rôle + mot de passe initial),
  réinitialisation de mot de passe, changement de rôle, suppression —
  garde-fous anti-lockout : impossible de modifier/supprimer SON propre
  compte (celui qui agit garde toujours la gestion). Audit
  `phase=account_created|password_reset|role_changed|account_deleted`.
  Uptime sur la tuile serveur (« depuis 3 j 14 h », `State.StartedAt`
  parsé — nanosecondes docker tronquées, an 1 = jamais démarré). Carte
  Jeu de l'accueil alignée sur /game (switches + info-bulles).
- **V6.5 « Un seul cerveau par donnée »** (fondations produit — diagnostic
  validé par Jeremy : le motif « donnée née en env/yaml + UI ajoutée
  ensuite = deux sources de vérité » s'est répété ; on converge) :
  - **V6.5.1 Comptes** : ✅ implémenté. `users.json` + `passwords.json`
    sont LA source de vérité ; roles.yml ne définit plus que les rôles
    (section `users:` importée UNE FOIS par `config.migrate_yaml_users` —
    rôle vers users.json, hash Argon2 copié tel quel vers passwords.json,
    drapeau `yaml_import_done` dans users.json ; comptes déjà présents,
    supprimés (tombstones V6.4) ou hash déjà changés en app : jamais
    écrasés). `app.state.credentials` supprimé, login/changement de mot de
    passe lisent le seul store. Effacer users.json = réimport (le drapeau
    vit dedans). Tombstones V6.4 : lus (migration) puis abandonnés à la
    première écriture.
  - **V6.5.2 Sauvegardes** : ✅ implémenté. Legacy V5 retiré : routes
    `/actions/backup` + `/actions/backup-schedule`, méthodes
    trigger_backup/trigger_scheduled_backup/set_backup_schedule/
    tick_scheduled_backup, ports `backup`/`scheduled_backup`/
    `backup_schedule` du service, `BackupSchedulePort`,
    `NotConfiguredBackup`, env `MC_BACKUP_CONTAINER`/
    `MC_SCHEDULED_BACKUP_CONTAINER`/`BACKUP_SCHEDULE_HOURS`, conteneurs
    mc-backup + mc-backup-scheduled du compose. BackupScheduler polle
    `tick_backup_profiles`. `restore_safety_backup` est un port explicite
    (absent -> null object `_NoBackup` du domaine). backup_schedule.json
    n'est plus lu que par seed_from_legacy (migration V5->V6 des profils).
    GARDÉ : mc-backup-safety (filet pré-restauration, auto-purgé, hors de
    portée d'écriture de mc-admin, voulu).
  - **V6.5.3 Métriques** : ✅ implémenté. DEFAULT_METRICS embarqués dans
    config.py (joueurs, latence, CPU, RAM, TPS, MSPT — `{container}`
    résolu depuis le serveur piloté servers.json, jamais en dur) ;
    metrics.yml devient un override optionnel qui REMPLACE tout (pas de
    fusion ; présent-mais-vide = panneau volontairement vide) ; le repo
    ne fournit plus que config/metrics.example.yml.
  - **V6.6 Emballage** : ✅ implémenté (17/07/2026). Décisions ACTÉES :
    nom public « mc-admin », licence MIT (LICENSE, © 2026 Jérémy
    Martinoty). `docker-compose.example.yml` générique 100 % variables
    (.env.example documenté), AUCUNE variable MINECRAFT_CONTAINER (le
    serveur se branche dans l'UI) ; vérifié en réel : stack jetable
    montée depuis l'exemple pur sur le NAS (setup → détection → consigne
    de profil écrite → conteneur backup démarré). README réécrit orienté
    produit (l'ancien était un journal de dev avec bootstrap roles.yml
    obsolète). CI GitHub Actions (.github/workflows/ci.yml) : ruff +
    pytest sur push/PR, image ghcr.io/jmartinoty/mc-admin (latest sur
    main, semver sur tags v*) — visibilité du package à passer en
    public dans l'UI GitHub (action manuelle Jeremy, une fois).
  - Ménage 17/07/2026 : roles.yml réduit aux rôles (users: sauvegardé
    dans data/roles.yml.pre-v65.bak puis retiré), legacy/ vide supprimé,
    identité git posée sur le clone local. Compte `paul` conservé (à
    supprimer dans l'UI si vestige).
- **V6.7 Performances (paliers 1+2)** : ✅ implémenté. Page /performance
  (Permission.STATUS — un viewer peut regarder) : tuiles TPS/MSPT
  moyen/max/entités, graphe MSPT 24 h avec SEUIL 50 ms (échelle qui
  inclut toujours 0 et le seuil, badge « seuil dépassé »), top 10 des
  entités par monde+type (`topk(10, sum by (world, type)
  (minecraft_entities) > 0)` — le `> 0` évite les zéros arbitraires sur
  serveur vide, état vide honnête « aucune entité chargée »), chunks par
  monde (valeur + sparkline 24 h). MetricsPort.performance() ->
  PerformanceSnapshot (requêtes FIGÉES dans l'adapter :
  _query_vector/_query_range_by ajoutés). Mondes traduits côté UI
  (overworld->Surface…). Dégradation : Prometheus down -> page
  « indisponible ». Palier 3 (spark par chunk via RCON) : à valider.
- **Renommage rôle par défaut** : `friend` -> `viewer` (retour Jeremy :
  « friends ça fait très perso » ; standard RBAC lecture seule).
  roles.yml custom non affecté ; compat : un users.json portant
  `friend` sur rôles PAR DÉFAUT est mappé viewer au chargement.
- **Surveillance des compagnons (17/07/2026)** : HealthWatcher étendu —
  `HEALTH_EXTRA_CONTAINERS` (ex. "playit") surveille des conteneurs en
  plus du serveur, seuils/anti-bruit identiques, alertes nominatives
  (« playit down »/« playit rétabli »). Motivation : tunnel playit
  resté mort 2 jours sans alerte. Le serveur surveillé est résolu via
  servers.json (comme build_service). Aussi : réseau mc_playit blindé
  (ip_range dynamique .192/26, statiques hors pool — prometheus avait
  volé l'IP fixe de playit pendant sa panne).
- **A2.1 Surveillance dans l'UI** : ✅ implémenté. La liste des
  compagnons devient une DONNÉE : `/data/watched_containers.json`
  (JsonWatched, amorçage one-shot depuis HEALTH_EXTRA_CONTAINERS —
  drapeau env_import_done, PAS d'écriture sur install fraîche sans env ;
  un nom retiré en UI ne ressuscite jamais). Le HealthWatcher RELIT le
  store à chaque sonde (ajout effectif ~30 s sans redémarrage, états
  anti-bruit conservés par compagnon, port_factory injectée). Service :
  watched_containers/watch_candidates/watch_container/unwatch_container
  (SERVER_MANAGE, audit phase=watch_added|removed, validation nom,
  serveur piloté refusé) + infra_status (STATUS — état honnête
  « injoignable » par conteneur, jamais d'exception). Page /watch
  (owner) : tableau surveillés + état live, ajout via listage COMPLET de
  l'hôte (ServerDiscoveryPort.list_all, même droit proxy). Carte
  « Infrastructure » sur l'accueil (tous rôles), affichée seulement s'il
  y a des compagnons. À suivre : A2.2 canaux de notification dans l'UI
  (notifier rechargeable), A2.3 interrupteurs d'événements.
- **A2.2/A2.3 Notifications dans l'UI** : ✅ implémenté. Canaux ET
  interrupteurs d'événements dans `/data/notifications.json`
  (JsonNotificationConfig, import one-shot des env DISCORD_WEBHOOK_URL/
  TELEGRAM_*/NOTIFY_PLAYER_EVENTS/HEALTH_ALERTS/NOTIFY_RESTORE_EVENTS —
  sans écriture sur install fraîche). `StoreBackedNotifier` : relit la
  config au changement (cache mtime) — modif UI effective au prochain
  envoi, AUCUNE reconstruction de threads (le verrou qui bloquait l'A2).
  Filtrage PAR ÉVÉNEMENT dans le notifier : notify(..., event=
  "player"|"health"|"restore") ; sans tag (échecs d'actions) = toujours
  envoyé, non débrayable. Conséquences : PlayerLogWatcher toujours
  notifié, HealthWatcher TOUJOURS démarré (l'interrupteur « health »
  filtre, le thread coûte une sonde/30 s),
  AdminService.notify_restore_events supprimé (funnel _notify_restore
  tague). UI sur /watch : canaux (validation https pour Discord, audit
  SANS secrets — présent/absent seulement), interrupteurs (switches +
  info-bulles), bouton « Envoyer un test » par canal configuré (envoi
  DIRECT hors filtre, résultat honnête en flash, audité
  phase=notify_test).
- **UI-1..4 (17/07/2026, analyse UX complète validée)** : ✅ implémenté.
  UI-1 : adresse de connexion PUBLIQUE par serveur (champ ServerEntry.
  address, éditable page Serveurs, audité ; chip mono + bouton copier sur
  la tuile, tous rôles — retour Jeremy « à aucun endroit on a l'IP ») ;
  sémantique boutons rétablie (le bouton par défaut EST le primaire vert,
  danger réservé au destructif/perturbateur) ; sidebar regroupée
  (implicite Jouer / « Protéger » / « Administrer »), « Réglages »
  renommé « Jeu ». UI-3 : /fragments/vitals pollé 5 s (pastille+uptime
  #vitalsState, Infrastructure #vitalsInfra — rendu serveur, échec
  réseau silencieux). UI-2 : dialogue de confirmation GLOBAL #appConfirm
  dans le shell (js-confirm-form + data-confirm, déclencheur bouton OU
  submit) — dialogues locaux users/backups supprimés et 9 confirm()
  NATIFS convertis (stop, update, op×3, bans×2, whitelist, console) ;
  test de non-régression qui scanne les templates. UI-4 : breakpoints
  900/640 px (grilles 1 colonne, tables resserrées, labels de groupes
  masqués en barre horizontale) — à VALIDER sur téléphone réel par
  Jeremy.
- Idée VALIDÉE à planifier (retours Jeremy 17/07) : NOTIFICATIONS EN
  PROFILS — page « Notifications » indépendante, canaux multiples à la
  manière des profils de sauvegarde (« ajouter un canal » → type
  Discord/Telegram + assistant de config) avec filtres d'événements PAR
  CANAL (ex. joueurs sur Telegram mais pas Discord). Fondation existante
  : notifications.json + StoreBackedNotifier (passer channels de dict à
  LISTE de {type, config, events}).
- **Notifications en profils (17/07/2026)** : ✅ implémenté.
  notifications.json passe au SCHÉMA V2 : liste de canaux {id, type,
  label, config, events, enabled} — filtres d'événements PAR CANAL (le
  cas Jeremy : joueurs sur Telegram, pas sur Discord). Types en liste
  FERMÉE dans le DOMAINE (NOTIFICATION_CHANNEL_TYPES : discord/telegram
  + champs requis ; EVENT_KEYS) — l'adapter les importe. Migrations
  one-shot à la lecture : V1 (dict A2.2) -> profils « importé » héritant
  des interrupteurs globaux ; env -> profils directement.
  StoreBackedNotifier filtre par canal (enabled + events), échecs
  d'actions toujours sur tous les canaux actifs. Service : CRUD complet
  (SERVER_MANAGE, audit sans secrets, validation champs/https).
  Page /notifications (sidebar Protéger) : cartes façon profils de
  sauvegarde (chips d'événements, Tester/Modifier/Suspendre/Supprimer),
  assistant 2 étapes (type -> config avec aide « où trouver mon
  webhook »), bouton « CRÉER ET TESTER » (test envoyé à la création,
  résultat honnête). /watch redevient pur (compagnons). Tests UI
  hermétiques (build_channel stubé — le test réel avait déclenché un
  appel réseau).
- **Filtres d'événements étendus + switches sur cartes (17/07/2026)** :
  ✅ implémenté. EVENT_KEYS passe à 8 : player, NEW_PLAYER (première
  connexion jamais vue — is_known() interrogé AVANT record_join),
  MODERATION (kick/ban/pardon/op), BACKUP (succès avec profil+taille,
  dans _finalize_backup), RESTART (succès — couvre manuel/planifié/
  quotidien via le funnel restart()), UPDATE (l'ex-toujours-envoyé,
  désormais débrayable), health, restore. EVENT_LEGACY_DEFAULTS :
  update=True pour une clé absente (comportement préservé), le reste
  opt-in (pas de spam surprise). Les cartes de canaux portent leurs 8
  switches INLINE (route granulaire /event, bascule immédiate) ;
  libellés centralisés (EVENT_LABELS, dashboard.py) partagés carte +
  assistant ; formulaires add/update lisent les 8 clés via
  _events_from_request. Reporté (itération « perf avancée » avec le
  palier 3 spark) : filtres performance (MSPT soutenu) et espace disque.
- **Perf avancée (17/07/2026)** : ✅ implémenté. VALIDATION RCON de
  spark faite en réel : les réponses spark via RCON sont MUETTES (async)
  et l'upload du mode --timeout ne laisse AUCUNE trace ; seul chemin
  fiable = `spark profiler stop --save-to-file` -> profile-*.sparkprofile
  dans config/spark/ (+ activity.json). Design : carte « Profil spark »
  sur /performance (RCON_RAW) — start avec FILET `--timeout N+30`
  (onglet fermé = spark s'arrête seul), compte à rebours côté page qui
  déclenche le stop, fichiers listés/téléchargeables (motif strict
  anti-traversal) à glisser sur spark.lucko.me. Montage
  config/spark:/spark:ro + MC_SPARK_DIR (vide = carte absente).
  PerfWatcher (app/perf_watcher.py, toujours actif, famille
  HealthWatcher) : MSPT moyen > 50 ms sur 3 sondes/60 s -> event
  `performance` (+ rétablissement) ; espace libre du volume sauvegardes
  < 10 Gio -> event `disk` (hystérésis +20 %). EVENT_KEYS = 10.
- **Assistant Telegram durci (17/07/2026, incident réel Jeremy)** :
  ✅ implémenté. Vécu : chat_id = @nom du bot (403 « can't send messages
  to the bot ») + double-clic sur « Créer et tester » (l'envoi du test
  bloque la requête) = 2 canaux. Correctifs : bouton désactivé à la
  soumission, doublon exact type+config REFUSÉ côté service, erreurs
  API traduites en correctif actionnable (_explain_channel_error), aide
  en 3 étapes avec /start en gras, et bouton « DÉTECTER MES CHATS »
  (adapters.telegram_detect_chats : getUpdates -> tous les chats
  dédupliqués {id, name, type}, sélecteur si plusieurs — le jeton vient
  du formulaire, jamais au journal). Rappel mécanique Telegram : jeton
  = expéditeur, chat_id = destinataire NUMÉRIQUE, /start obligatoire.
- **Mods enrichis Modrinth (18/07/2026)** : ✅ implémenté. Identification
  EXACTE par SHA-1 des .jar (ModInfo.sha1, calcul en flux cachés par
  mtime/taille) — jamais par nom. ModrinthCatalog : 2 POST par passe
  (version_files -> version installée ; version_files/update -> dernière
  compatible loader fabric + version MC courante via
  current_server_version(), helper extrait de MinecraftUpdater).
  ModUpdateChecker (thread famille archive-verifier) : première passe
  60 s après boot, puis AU PLUS une interrogation par 24 h (sauf jar
  nouveau/changé) ; version serveur inconnue OU erreur réseau = on garde
  les derniers verdicts (JsonModChecks, /data/mod_checks.json, clé =
  sha1). Page Mods : colonne Modrinth (badges à jour/màj dispo + lien,
  « inconnu de Modrinth » honnête, « vérification à venir »), date de
  dernière passe. LECTURE SEULE : mc-admin ne met jamais un mod à jour
  (jar = redémarrage + compat, geste humain). User-Agent identifiant
  requis par l'API Modrinth.
- **Garde-fous & finitions (18/07/2026 soir)** : ✅ implémenté.
  `WorkerIntegrityPort`/`DockerWorkerIntegrity` — les one-shots pré-créés
  sont comparés au conteneur mc-admin courant (image, et réseaux PAR NOM
  via les seules inspections autorisées — NETWORKS=0 interdit /networks,
  la comparaison croisée des NetworkID suffit) ; préflight de
  restauration FAIL-CLOSED (désalignement OU vérification impossible =
  refus), déclenchement de sauvegarde bloqué sur désalignement CONFIRMÉ
  seulement, lignes de diagnostic sur la page Serveurs. Motif : 3
  incidents le 18/07 (2× image périmée, 1× réseau mc_playit recréé —
  sauvegarde nocturne échouée). `flash_error()` (routes/common.py) :
  message court + code stable (BKP/RST/JOU/SRV/JEU/SUR/NOT/PRF-nn) +
  dialogue « Détails » copiable — 33 sites `{exc}` convertis, les tests
  s'assoient sur les codes, plus jamais de 404 brut en toast. Journal :
  ligne JSONL corrompue ignorée et comptée (warning), la page Journal
  s'affiche toujours.
- **Test réel du worker de restauration (nuit du 18 au 19/07/2026)** :
  ✅ RÉUSSI. Protocole gamerule (0 joueur vérifié, marqueur
  random_tick_speed 3→5, save-all, consigne écrite, mc-restore démarré
  hors app — le worker seul était la pièce jamais exercée) : extraction
  vérifiée AVANT arrêt, rollback local, sync en place, exit 0, serveur
  revenu healthy, marqueur revenu à 3, zéro fichier transactionnel
  résiduel. Le flux APP complet (préflight, sauvegarde de sécurité
  séquencée) reste couvert par les tests + le réel des 12-13/07.
- Ensuite : essai BlueMap (nuit de rendu bridé, verdict via page
  Performances), release v1.0, V7 multi-serveur + mode hôte sans Docker.
  Backlog complet priorisé (fiabilité + UX, arbitré le 18/07/2026) :
  **`docs/roadmap.md`** — s'y référer avant de proposer un nouveau
  chantier, et le tenir à jour à chaque lot livré.

### Mode maintenance + portier `mc-doorman` (20/07/2026)

- **✅ Livré (mode manuel)**. Fermer le serveur SANS le rendre muet : Minecraft
  est arrêté, mais son endpoint réseau reste tenu par le portier `mc-doorman`,
  qui répond aux joueurs à sa place (MOTD « Maintenance » dans la liste des
  serveurs + refus de connexion expliqué au lieu d'un « connexion refusée »).
- `app/doorman_server.py` : mini-serveur **stdlib** (aucune dépendance) parlant
  juste assez le protocole Java Edition — handshake, status (MOTD + ping/pong),
  refus au login/transfert. Le protocole du CLIENT est renvoyé tel quel dans
  `version.protocol` : le serveur apparaît « compatible » et le joueur lit le
  MOTD au lieu d'un « version incompatible ». Bornes anti-abus (taille de
  paquet/chaîne, timeout) : le port est exposé aux joueurs, tout octet hostile
  ferme la connexion sans tuer le portier.
- ⚠️ **L'IP STATIQUE est le cœur du design** : le tunnel playit cible l'adresse
  **en dur** `172.29.0.10:25565` (API playit, PAS le nom docker) — vérifié au
  spike du 19/07. Le portier doit donc REPRENDRE l'IP de minecraft, hors du
  pool dynamique du réseau (`172.29.0.192/26`). Conséquence assumée et
  structurante : `minecraft` et `mc-doorman` ne peuvent JAMAIS tourner
  ensemble (Docker refuse la seconde attribution) — c'est le garde-fou, le
  portier ne peut pas voler l'adresse d'un serveur vivant.
- **Zéro droit Docker pour le portier** (contrairement à mc-restore) : il lit
  sa consigne dans un fichier (`/data/doorman.json`, écrit par mc-admin AVANT
  le démarrage — pattern mc-restore/backup-profile) et sert des octets.
  mc-admin le démarre/arrête via le proxy avec les permissions start/stop
  qu'il a DÉJÀ : la whitelist du socket-proxy est INCHANGÉE.
- Service (`domain/services/maintenance.py`) — trois garde-fous :
  1. l'état « en maintenance » est toujours RELU du portier réel
     (`DoormanPort.is_running()`), jamais déduit d'une variable locale ;
  2. on ne redémarre JAMAIS le serveur avant que le portier ait rendu
     l'adresse (`release()` attend la libération effective et lève sinon) ;
  3. si l'arrêt réussit mais que le portier échoue, le serveur reste arrêté,
     l'échec est audité ET notifié — « Rouvrir » est le chemin de reprise.
- Permission dédiée **`MAINTENANCE`** (pas de réemploi de `STOP` : arrêt sec
  définitif vs fermeture annoncée réversible — l'audit doit les distinguer).
  Notifications sur l'événement existant `restart` (même famille
  « disponibilité »), pas de 11e interrupteur.
- Arrêt avec **`timeout=120`** (`ContainerPort.stop(timeout=…)`, nouveau
  paramètre optionnel) : le défaut docker de 10 s tuait Minecraft en pleine
  sauvegarde de ses mondes.
- Délai de grâce optionnel : `InMemoryPendingMaintenance` (non persistant,
  même compromis que le redémarrage programmé — rien n'est fermé tant que
  l'échéance n'est pas atteinte) + `MaintenanceScheduler` (thread de fond,
  famille `RestartWarningScheduler`) qui diffuse des avertissements dégressifs
  puis ferme.
- Création du one-shot :
  `docker compose --profile tools create --force-recreate mc-doorman`
  (il est aussi couvert par le `WorkerIntegrityPort` : image et réseau
  comparés à mc-admin, cf. piège des workers périmés).
- **Vérifié en réel le 20/07** : portier lancé dans le vrai réseau `mc_playit`
  et interrogé par un client parlant le protocole — MOTD correct, ping/pong
  renvoyé, refus au login avec le message de la consigne.
- **RESTE À FAIRE** : portier automatique pendant une restauration
  (`docs/roadmap.md` n° 7b) — délibérément séparé, le worker a 8 points
  d'arrêt/démarrage et mérite sa propre itération testée.

### Roadmap V5.x (consolidations post-V5 — ✅ livrées au fil de l'eau)

- **Page Réglages (`/game`)** : toutes les gamerules du serveur (58 en MC
  26.1), barrière `Permission.GAME_SETTINGS`. La liste des noms vient de
  `help gamerule` (`RconGame.list_gamerule_names`) et sert de **whitelist
  dynamique** au domaine (`AdminService.set_gamerule`) ; valeurs booléennes
  (toggle) et numériques (bornées ±1 000 000). Lecture groupée sur **UNE**
  connexion RCON (`get_gamerules`). La carte « Jeu » du dashboard garde 3
  règles usuelles + difficulté/météo/heure.
  - ⚠️ **MC 26.1 a renommé les gamerules en snake_case** (`keepInventory` →
    `keep_inventory`, `doDaylightCycle` → `advance_time`) : la syntaxe de
    lecture `gamerule <nom>` ne marche qu'avec les NOUVEAUX noms — vérifié
    contre le vrai RCON, ne pas se fier aux docs antérieures à 26.1.
  - ⚠️ **Protocole RCON — deux pièges vanilla constatés en réel** (fix dans
    `adapters/rcon.py`) : les réponses > 4096 octets arrivent **fragmentées**
    (même id, ex. `help gamerule`) — un paquet plein déclenche une lecture
    bornée par commande sentinelle vide ; et vanilla **ferme la connexion si
    deux paquets RCON partagent un segment TCP** — la sentinelle n'est donc
    envoyée qu'APRÈS réception de la première réponse, jamais en rafale.
- **`routes.py` éclaté en package** `app/api/routes/` (1041 lignes → modules
  `common/auth/dashboard/players/backups/audit`), import public inchangé
  (`from api.routes import router`). Les redirections POST passent par
  `_redirect_target` (liste blanche de chemins internes — jamais le Referer).
- **Sauvegardes consolidées** : convention de nommage `mc-manual-*` /
  `mc-auto-*` (env `BACKUP_NAME` par conteneur), anciennes archives PaperMC
  rangées dans `legacy/` (⚠️ layout interne pré-migration : **ne jamais les
  restaurer** via mc-restore). Programmation **persistée**
  (`/data/backup_schedule.json`) réglable dans l'UI : intervalle en heures OU
  heure quotidienne fixe (avec rattrapage si l'heure est déjà passée le jour
  même — asymétrie assumée avec le redémarrage quotidien, qui ne rattrape
  pas). Rétention **réellement appliquée** (plus un dry-run), physiquement
  bornée à `scheduled/` : montage imbriqué `/backups` RO + `/backups/scheduled`
  RW — impossible de supprimer une archive manuelle même en cas de bug.
- **Logs / terminal** : historique au-delà de la rotation (backfill des
  archives `.gz` de `logs/`, cache par mtime), affichage borné au **dernier
  démarrage du serveur** (BOOT_MARKER), filtre anti-bruit RCON (chaque
  commande mc-admin polluait `latest.log`), coloration par niveau + filtres
  texte/niveau côté client (réappliqués après chaque swap du fragment pollé).
  Après une commande console, redirection vers `/#terminal` (pas de retour en
  haut de page).
- **Journal** : onglet « Événements » (timeline joueurs) DANS la page Journal
  (pas une page à part) ; pseudos affichés résolus à côté de l'identité
  d'audit (« jeremy · owner »), qui reste la clé.
- **Notifications événements** : « joueur rejoint/part »
  (`NOTIFY_PLAYER_EVENTS`) et « serveur down » (`HEALTH_ALERTS`,
  `app/health_watcher.py`). **Activées le 12/07/2026** (flags à `true` dans
  le `.env` du NAS) avec l'anti-spam choisi par Jeremy : **cooldown 15 min
  par joueur** (`player_watcher.py`, l'historique enregistre tout, seule la
  notification est limitée) ; le health watcher avait déjà son anti-bruit
  (3 sondes down consécutives, une alerte par chute + un rétablissement).
- **Sparklines 24 h** sur les tuiles métriques (TPS/RAM/joueurs) : entrées
  `spark: true` de `config/metrics.yml` → `query_range` Prometheus (pas
  30 min) dans `MetricReading.history`, best-effort (échec = valeur seule).
  SVG généré par le helper de vue `spark_points` (couche présentation).
  A remplacé le zigzag décoratif `::after` des tuiles.
- **Journal v2 — flux d'opérations** (14/07/2026) : une ligne = une
  OPÉRATION. Les événements partageant un `operation_id` (restaurations)
  sont repliés en une ligne dépliable (étapes en mini-timeline, design
  repris de l'ancien onglet Timeline — supprimé, ainsi que la section
  « Restaurations récentes », redondants). 4 colonnes (Quand/Qui/Opération/
  Statut) : plus de colonne Cible ni Résultat technique — pastilles
  françaises (fait/refusé/échec/réussie/en cours). Détails UNIFIÉS à
  l'affichage (`_operation_line`, couche présentation : le JSONL brut
  reste la source de vérité, visible en dépliant) ; heures LOCALES
  (le bug UTC venait de strftime sans astimezone) ; séparateurs de jours ;
  familles d'événements (chips colorées + filtres rapides, refus RBAC
  toujours classé Sécurité) ; badge « auto » sur les comptes système.
  Filtres utilisateur/action/résultat/texte/période auto-appliqués,
  pagination 50/page, fenêtre 2000 entrées.
  **Compléments (14/07/2026)** : statuts STANDARDISÉS (réussi/échec/refusé/
  en cours — « fait » ne disait pas si ça avait marché) ; la sous-étape
  fautive d'une opération en échec remonte dans la ligne repliée ; les
  ISSUES de sauvegardes sont auditées (`backup_done`/`backup_failed` sous
  l'identité `backup-watch`, constatées par `_scan_backup_outcomes` appelé
  par le polling ET le coordinateur de fond — une sauvegarde nocturne ratée
  laisse sa trace) et groupées avec leur déclenchement par operation_id ;
  redémarrages programmés groupés (op id dérivé de l'échéance, sans état) ;
  ligne de synthèse (échecs/refus cliquables) ; famille « En jeu » :
  connexions/déconnexions ET commandes des joueurs (`issued server
  command`, capté par le watcher, table SQLite `player_commands`) fusionnées
  au flux — exclues quand un filtre purement audit est actif ; ROTATION
  MENSUELLE du JSONL (`audit-AAAA-MM.jsonl`, l'ancien fichier unique reste
  lu comme archive, rien n'est supprimé).
- **Anti-bruteforce login** (`app/api/routes/auth.py`) : fenêtre glissante en
  mémoire par utilisateur — 5 échecs / 10 min → verrou 5 min (HTTP 429).
  En mémoire = perdu au redémarrage : suffisant pour du LAN, assumé.
- **Stats vanilla des joueurs** (`PlayerStatsPort`/`FilePlayerStats`) :
  `<monde>/players/stats/*.json` + `usercache.json` montés RO (chemins dans
  le compose — attention, le monde s'appelle `old_world_1`). Temps de jeu
  TOTAL compté par le serveur depuis la création du monde (couvre l'ère
  pré-mc-admin, y compris PaperMC : le monde a été migré), affiché sur
  `/players` (prioritaire sur le temps observé par le tailer) et fiche
  joueur enrichie (morts, kills, distance, blocs minés…). Best-effort
  intégral : fichier corrompu = joueur ignoré, uuid expiré du usercache =
  pseudo de repli (uuid tronqué). L'import des archives de logs a été
  étudié et écarté : ~2 jours de gain seulement (archives depuis le
  01/07, base sessions depuis le 03/07).
- **Assistant de restauration** (merge branche Codex `feat/restore-assistant`,
  13/07/2026) : préflight non mutant (`restore_preflight`), sauvegarde de
  sécurité **dédiée** (`mc-backup-safety` → `restore-safety/`, hors de portée
  de la rétention ET des droits d'écriture de mc-admin — il se purge lui-même,
  `PRUNE_BACKUPS_DAYS=14`), machine à états suivie jusqu'au retour healthy de
  Minecraft (conteneur + RCON), progression estimée par taille d'archive,
  événements d'audit structurés (`phase=… archive=… operation_id=…`) traduits
  en français dans le Journal. `NOTIFY_RESTORE_EVENTS` (défaut false).
  Testé en réel de bout en bout le 13/07/2026.
- **Bandeau d'activité sauvegardes** (retour Jeremy 13/07/2026) : le SERVEUR
  est l'unique source de vérité (même payload `_activity_payload` pour le
  rendu initial et le polling JS — plus aucun localStorage). Titre =
  l'opération (stable), message = l'étape numérotée (1/3 → 3/3, textes dans
  le service). Le résultat (succès/échec) reste affiché **jusqu'à fermeture
  par la croix** (`dismiss_restore_result`, non audité — pur affichage) ; le
  JS ne fait que rafraîchir l'étape et recharger sur transition d'état.
- **Intégrité des archives** : `ArchiveVerifier` (thread de fond, famille
  watchers) relit chaque archive en entier (tar+gzip en flux), UNE par passe
  (60 s) et jamais avant 3 min d'âge (écriture en cours) ; verdicts persistés
  (`/data/archive_checks.json`, `JsonArchiveChecks`) et affichés en badge
  (✓ vérifiée / ⚠ illisible). Le plan de rétention ne compte plus que les
  archives AUTO (les protégées sont hors périmètre, plus de compteur
  trompeur) ; ligne d'espace disque en tête de liste (`free_bytes`).
- **Sidebar** : groupe « Gestion » (Joueurs `/players` + Réglages `/game`).
- **Redémarrage quotidien récurrent** persisté (`/data/recurring_restart.json`,
  UI dans le dialog Redémarrer) — distinct du redémarrage programmé ponctuel
  V4.5 (lui volontairement non persistant).

---

## 9. Déploiement

- Repo de production hébergé hors `AppData/` :
  **`/Volume1/Projects/mc-admin/`** sur le TerraMaster. Le remote privé `nas`
  reçoit les commits déployables. Le remote GitHub `origin`
  (`jmartinoty/mc-admin`) est destiné à pouvoir devenir public : aucune
  information personnelle ou secrète n'y est admise et aucun push n'y est fait
  sans demande explicite de Jeremy (workflow complet : §0).
- Image **publiée** `ghcr.io/jmartinoty/mc-admin:latest` (install « end-user »
  depuis le 27/07/2026 — plus de build local, mise à jour par le bouton MAJ
  de l'UI, cf. §0). Port hôte `3011` → `8000` conteneur. Reverse-proxy : **`https://mc-admin.home`** (NPM,
  certificat wildcard **mkcert** `*.home`, expire **octobre 2028**). Accès
  direct `http://<IP-NAS>:3011` conservé pour le healthcheck Docker, mais le
  login n'y fonctionne plus (cookie `Secure`, cf. §7.5).
  - Pas de domaine public : la CA mkcert doit être approuvée sur chaque
    appareil client (`mkcert -install`, ou import du `rootCA.pem` dans le
    trousseau/magasin système). Renouvellement du certificat (même principe,
    tourne sur le poste de dev qui détient la CA — **jamais committée**) :
    `mkcert -cert-file home-services.pem -key-file home-services-key.pem "*.home" mc-admin.home nas-dashboard.home`
    puis réimporter dans NPM.
  - **Accès distant : ✅ en place depuis le 14/07/2026** via Tailscale
    (`tailscale serve` HTTPS sur un port dédié du nœud NAS, proxy vers
    127.0.0.1:3011 — URL, port et procédure exacts : `docs/dev-depuis-mac.md`,
    non versionné). **Tailnet only** : PAS exposé à Internet — ne jamais
    monter mc-admin derrière un Funnel public. Certificat Let's Encrypt
    automatique (aucune CA à installer, valide iPhone/Mac). ⚠️ Le client doit
    utiliser **MagicDNS** (« Use Tailscale DNS ») : sinon le nom du nœud
    résout vers ses IP publiques où le port servi n'existe pas
    (`handleIngress: unconfigured … rejecting` dans les logs — constaté).
    Ami distant : PARTAGE du nœud depuis la console admin Tailscale + son
    compte mc-admin habituel. Désactivation : `tailscale serve
    --https=<port> off`.
- `docker compose` : services `mc-admin` + `mc-socket-proxy` ; réseaux
  `mc_admin_internal` (app↔proxy) + `mc_playit` (external, app→RCON minecraft)
  + `monitoring` (external, app→Prometheus).

### ⚠️ Dev depuis un poste distant (`docker context ssh://…`)

Le dev se fait depuis un Mac, Docker piloté à distance sur le TerraMaster via
l'alias SSH `NAS-ts`. Préférer `docker --host ssh://NAS-ts ...` aux contextes
Docker locaux qui peuvent être absents ou périmés (cf.
`docs/dev-depuis-mac.md`, non versionné). **Piège
rencontré en réel** : les volumes en chemin **relatif** (`./config`) sont
résolus par le **CLIENT** (le poste de dev) et envoyés tels quels au daemon
distant, qui ne les trouve pas sur son propre filesystem (le conteneur démarre
avec un `/config` vide → `FileNotFoundError`). **Tous les volumes de ce compose
utilisent donc des chemins ABSOLUS côté NAS** (`/Volume1/...`) — ne jamais
réintroduire de chemin relatif ici.

⚠️ **Le même piège existe dans les AUTRES stacks** (ex. `AppData/monitoring/
docker-compose.yml`, corrigé en V3.5) — tout compose non retouché depuis la
migration Mac peut encore avoir des chemins relatifs jamais exercés. Vérifier
au premier `docker compose up` d'un ancien fichier.

⚠️ **Piège apparenté — propriété des fichiers** : un fichier téléchargé/créé
depuis le Mac (via le montage SMB) appartient à l'utilisateur du Mac mappé
côté NAS, PAS forcément à l'uid attendu par le conteneur (souvent 1000:1000
pour les images type itzg). Comparer avec un fichier existant qui fonctionne
(`ls -la` côté NAS via `ssh NAS-ts`) avant de blâmer autre chose qu'un `chown`.
