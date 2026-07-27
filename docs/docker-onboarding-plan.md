# Installation Docker et onboarding serveur

> Plan d'implementation valide par Jeremy le 20/07/2026.
>
> Ce document est la source de verite du chantier. Il decrit le produit cible,
> les contraintes Docker, les garde-fous et les criteres d'acceptation. Les
> decisions ci-dessous ne doivent pas etre reouvertes pendant l'implementation
> sans en parler a Jeremy.

## 1. Objectif

Permettre a une personne qui ne connait pas l'architecture interne de mc-admin
de partir d'un dossier vide et d'obtenir une instance fonctionnelle avec :

1. `docker compose up -d` ;
2. creation du premier compte administrateur dans le navigateur ;
3. detection d'un serveur Minecraft Docker deja actif ;
4. configuration RCON automatique quand `server.properties` est accessible ;
5. verification guidee des capacites disponibles ;
6. import facultatif d'un historique joueurs ;
7. creation d'une premiere sauvegarde verifiee ;
8. acces au tableau de bord.

Une installation neuve utilise un volume mc-admin vide. Elle ne copie, ne
deplace et ne modifie aucune donnee d'une instance mc-admin existante.

## 2. Perimetre de la premiere version

### Inclus

- Docker Compose sur le meme Docker Engine que Minecraft.
- Un seul serveur Minecraft actif par instance mc-admin.
- Detection des conteneurs Minecraft en cours ou arretes.
- Images `itzg/minecraft-server` prises en charge en priorite.
- Formulaire manuel comme solution de secours.
- RCON configure depuis `server.properties` quand c'est possible.
- Bind mounts et volumes Docker nommes.
- Import d'un ancien `players.db` mc-admin.
- Import de `*.log.gz` et `latest.log`.
- Lecture des stats vanilla, du usercache, de la whitelist, des OP et des bans.
- Laboratoire complet avec un serveur Minecraft jetable.

### Hors perimetre

- Plusieurs serveurs pilotes par la meme instance.
- Serveur distant hors du Docker Engine local.
- Kubernetes, Swarm et installation native sans Docker.
- Modification silencieuse de la configuration Minecraft.
- Redemarrage automatique de Minecraft pendant l'onboarding.
- Import arbitraire de fichiers ou execution de commandes fournies par l'UI.

## 3. Decisions structurantes

| Sujet | Decision |
|---|---|
| Isolation | Chaque installation est namespacee par son projet Compose. |
| Noms Docker | Aucun `container_name` fixe dans le compose public. |
| Workers | Resolution par labels d'instance et de role, jamais par nom global. |
| Socket Docker | Le web garde le socket-proxy restreint. |
| Helper | Le socket brut n'existe que dans un conteneur one-shot a commande figee. |
| Secrets | Fichiers sous `/data/secrets`, permissions `0600`, jamais dans l'audit. |
| Session | Secret genere et persiste automatiquement au premier demarrage. |
| RCON | Detection automatique, saisie manuelle seulement en repli. |
| Activation | Reconfiguration/recreation automatique de mc-admin apres validation. |
| Donnees | Le laboratoire utilise un volume neuf et un Minecraft jetable. |
| Restauration | Jamais testee contre le monde de production. |

## 4. Parcours utilisateur

### Etape 1 - Compte

- Creer le premier owner.
- Persister le compte avant d'ouvrir la session.
- Si aucun serveur n'est configure, rediriger vers l'onboarding.

### Etape 2 - Serveur detecte

- Scanner les conteneurs via le socket-proxy.
- Identifier Minecraft par image, labels et ports.
- Afficher nom, image, etat et edition probable.
- Presselectionner un candidat unique sans l'ajouter silencieusement.
- Proposer "Configurer manuellement" si aucun candidat n'est fiable.

### Etape 3 - Preparation

- Enregistrer une requete d'installation bornee contenant uniquement l'identite
  Docker du conteneur selectionne et un nom d'affichage valide.
- Demarrer le helper one-shot precree.
- Afficher un suivi persistant pendant la recreation de mc-admin.
- Reprendre automatiquement le parcours apres reconnexion.

### Etape 4 - Capacites

Verifier et afficher separement :

- inspection et controle Docker ;
- connexion et authentification RCON ;
- lecture des logs ;
- lecture de `usercache.json` et des stats ;
- lecture des mods, OP et bans ;
- destination de sauvegarde disponible ;
- workers de sauvegarde/restauration correctement montes ;
- espace disque suffisant.

Une capacite absente ne doit pas produire une page cassee. L'assistant explique
ce qui manque et les fonctions concernees.

### Etape 5 - Historique

- Proposer "Importer maintenant" et "Plus tard".
- Toujours analyser avant d'ecrire.
- Afficher periode, fuseau, joueurs, sessions, heures et conflits.
- Rendre l'import des commandes joueurs facultatif.
- Creer une copie de securite de la base avant application.

### Etape 6 - Protection initiale

- Choisir ou confirmer la destination des archives.
- Creer un profil par defaut explicite.
- Lancer une sauvegarde.
- Relire l'archive et afficher son verdict.
- Ne pas proposer une restauration reelle pendant l'onboarding.

## 5. Configuration automatique de RCON

Le protocole RCON ne permet pas de decouvrir un mot de passe. Le helper lit
donc uniquement `server.properties` depuis le conteneur selectionne.

Ordre de recherche :

1. `/data/server.properties` ;
2. chemins connus des images explicitement prises en charge ;
3. racines des volumes detectes ;
4. saisie manuelle si aucun fichier fiable n'est trouve.

Valeurs lues :

- `enable-rcon` ;
- `rcon.port` ;
- `rcon.password` ;
- `level-name`.

Regles :

- ne jamais journaliser le contenu du fichier ;
- ne jamais afficher le mot de passe ;
- ne jamais inspecter ou restituer l'ensemble des variables d'environnement du
  conteneur Minecraft ;
- ecrire le secret avec un remplacement atomique et le mode `0600` ;
- tester avec une commande non destructive (`list`) ;
- si RCON est desactive, expliquer la correction mais ne rien modifier sans
  validation explicite.

## 6. Helper Docker one-shot

Le helper est privilegie, mais inerte au repos. Son image et sa commande sont
figees dans Compose. Le web peut uniquement ecrire une requete validee et
demarrer ce conteneur deja cree.

Responsabilites :

1. relire l'identite du conteneur demande ;
2. verifier qu'il ressemble toujours a Minecraft ;
3. inspecter ses reseaux et volumes ;
4. lire le seul fichier `server.properties` ;
5. construire un modele de configuration interne ;
6. generer `compose.generated.yml` avec PyYAML ;
7. connecter mc-admin au reseau retenu ;
8. monter les donnees Minecraft en lecture seule dans le web ;
9. monter les volumes strictement necessaires dans les workers ;
10. recreer les services de cette instance seulement ;
11. ecrire un resultat persistant succes/echec.

Interdictions :

- aucune commande, option Docker, URL ou chemin hote libre venant du navigateur ;
- aucune interpolation de chaine pour fabriquer du YAML ou une commande shell ;
- aucun controle d'un conteneur appartenant a une autre instance mc-admin ;
- aucune modification du compose Minecraft ;
- aucun redemarrage de Minecraft.

## 7. Persistance et migrations

### Secrets

Ajouter la prise en charge de :

- `SESSION_SECRET_FILE` ;
- un secret RCON par serveur ;
- une generation idempotente au demarrage ;
- une redaction centralisee des logs et erreurs.

### Serveur actif

Le registre des serveurs reste persiste, mais la V1 refuse un second serveur
actif. L'etat d'onboarding doit distinguer :

- aucun compte ;
- compte cree, aucun serveur ;
- installation en cours ;
- serveur configure mais incomplet ;
- installation terminee.

### Historique joueurs

Faire evoluer SQLite avec des migrations versionnees :

- table `schema_migrations` ;
- table `history_imports` avec empreinte, source, periode et resultat ;
- index sur joueur et horodatages ;
- identifiant d'origine pour rendre l'import idempotent.

L'import doit :

1. parser dans une structure temporaire ;
2. calculer un apercu ;
3. verifier les chevauchements ;
4. sauvegarder `players.db` ;
5. appliquer dans une transaction ;
6. restaurer automatiquement la copie en cas d'echec.

Le script `scripts/import_legacy_logs.py` fournit le parseur initial. La logique
metier reutilisable doit sortir du script ; le CLI et l'UI appellent ensuite le
meme service.

## 8. Plan de livraison

| Lot | Commit indicatif | Resultat attendu |
|---|---|---|
| 1 | `refactor(docker): namespace stacks and resolve workers by labels` | Deux piles coexistent sans conflit. |
| 2 | `feat(setup): persist generated secrets and bootstrap state` | Demarrage sans `.env` secret. |
| 3 | `feat(setup): detect minecraft containers and capabilities` | Candidats fiables et repli manuel. |
| 4 | `feat(setup): add one-shot docker configuration helper` | Reseaux et volumes appliques sans socket brut dans le web. |
| 5 | `feat(setup): configure and verify rcon automatically` | RCON fonctionnel sans saisie quand possible. |
| 6 | `feat(ui): add first-run server onboarding wizard` | Parcours complet et recuperable apres recreation. |
| 7 | `feat(players): import historical logs with preview and rollback` | Import idempotent et transactionnel. |
| 8 | `test(e2e): cover fresh docker installation and disposable restore` | Validation navigateur et Docker reelle. |
| 9 | `docs(install): document public docker deployment` | Installation depuis un dossier vide. |

Chaque lot est un commit autonome, avec tests cibles puis suite complete. Ne pas
melanger une refonte visuelle generale ou le multi-serveur a ce chantier.

## 9. Strategie de travail Claude/Codex

- Claude travaille dans un worktree et une branche `claude/docker-onboarding`.
- Aucun agent ne modifie le repertoire de production directement.
- Le laboratoire utilise un nom de projet, une image, un port et des volumes
  propres.
- Les changements Compose sont valides dans le laboratoire avant integration.
- Aucun deploiement de production n'est necessaire pour les lots documentaires
  ou les tests locaux.
- Si `main` avance, rebase ou fusion explicite dans le worktree ; ne jamais
  ecraser les changements de l'autre agent.

## 10. Matrice d'acceptation

### Installation

- Un dossier vide et le compose public suffisent.
- Aucun secret n'est requis dans `.env`.
- Deux installations peuvent tourner en parallele.
- Supprimer le laboratoire ne supprime aucune donnee d'une autre instance.

### Detection

- Un serveur itzg actif est detecte.
- Un conteneur non Minecraft n'est pas propose comme candidat principal.
- Un serveur arrete reste selectionnable.
- L'absence du proxy produit une erreur guidee, pas une liste trompeusement
  vide.

### RCON

- Hote, port, activation et mot de passe sont detectes sur le cas nominal.
- Le mot de passe n'apparait ni dans HTML, audit, logs, exception ou compose
  genere.
- Une mauvaise authentification laisse revenir au formulaire.
- Un serveur sans RCON n'est jamais redemarre automatiquement.

### Donnees

- Logs, stats, mods, OP et bans sont montes en lecture seule dans le web.
- Seuls les workers dedies obtiennent les ecritures necessaires.
- Une destination de sauvegarde de laboratoire ne pointe jamais vers les
  archives de production.

### Historique

- Le dry-run n'ecrit rien.
- Un import reexecute ne duplique rien.
- Un import en echec laisse la base d'origine intacte.
- Les commandes joueurs sont exclues par defaut ou demandees explicitement.

### UX

- L'utilisateur n'a pas a connaitre un nom de reseau Docker.
- Le jargon technique est place dans un detail repliable.
- Une recreation de mc-admin ne renvoie pas vers le debut.
- Les etats chargement, succes, echec et reprise sont utilisables au clavier et
  sur mobile.

## 11. Garde de publication

Ne pas publier une image stable destinee a des tiers avant :

- correction de l'isolation du proxy de carte ;
- serialisation fiable des sauvegardes de profils ;
- gestion de la rotation et des erreurs du watcher joueurs ;
- controle complet des workers avant action destructive ;
- tests d'installation et de restauration sur le serveur jetable ;
- image versionnee, jamais uniquement `latest` ;
- procedure documentee de mise a jour et de retour arriere.
