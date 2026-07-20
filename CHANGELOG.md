# Changelog

Les versions suivent [semver](https://semver.org/lang/fr/). La série 0.x est
une bêta publique : l'outil est utilisé en production chez son auteur, mais
des morceaux annoncés manquent encore (voir la roadmap dans `docs/`).

## v0.10.0 — 2026-07-20

### Mode maintenance (nouveau)
- Fermer le serveur SANS le rendre muet : un portier (`mc-doorman`) reprend
  son adresse réseau pendant l'arrêt et répond aux joueurs — MOTD
  « Maintenance » dans la liste des serveurs et refus de connexion expliqué,
  au lieu d'un « connexion refusée » brut. Fonctionne aussi derrière un
  tunnel (playit) qui cible une IP fixe.
- Message et heure de retour personnalisables, délai de grâce optionnel avec
  avertissements dégressifs in-game avant la fermeture.
- Bandeau visible de tous pendant la maintenance ; « Rouvrir » relève le
  portier puis relance le serveur (jamais l'inverse : l'adresse est rendue
  d'abord). Permission dédiée `MAINTENANCE`.
- Nouveau service compose `mc-doorman` (profil `tools`) et variable
  `MC_DOORMAN_IP` — voir `docker-compose.example.yml`.

### Mise à jour de mc-admin en un clic (nouveau)
- Détection automatique des nouvelles versions (une vérification par jour),
  carte sur l'accueil de l'owner avec les notes de version.
- Application en un clic pour les installations par image (ghcr) : un
  one-shot dédié tire la nouvelle image et recrée mc-admin ET ses outils —
  fini les workers restés sur une image périmée. Refus si une sauvegarde ou
  une restauration est en cours ; installations « build local » : détection
  seule, avec explication.

### Carte, joueurs, performances
- Page Carte v2 : mc-admin sert la carte (BlueMap, Dynmap…) sous sa propre
  origine — plus de blocage de contenu mixte HTTP/HTTPS, fonctionne en LAN
  comme à distance ; assistant d'activation (détection, test, activation) ;
  cache navigateur préservé (déplacements fluides).
- Joueurs : recherche par pseudo et tris (en ligne, dernière connexion,
  temps de jeu, A→Z), combinés aux filtres existants.
- Graphe MSPT honnête : un pic ne peut plus se cacher entre deux points
  (max par fenêtre), axe horaire, plages 24 h / 3 j / 7 j.

### Interne
- `services.py` découpé en package `domain/services/` (9 modules par thème,
  import public inchangé).
- Arrêt du conteneur serveur avec 120 s de grâce (le défaut docker de 10 s
  pouvait interrompre une sauvegarde des mondes en plein vol).

## v0.9.0 — 2026-07-19 (première bêta publique)

Première version publiée. État du produit à cette date :

### Administration
- Tableau de bord vivant (état du serveur, joueurs en ligne, uptime,
  adresse de connexion partageable, actions démarrer/redémarrer/arrêter).
- Terminal continu : logs colorés + console RCON (owner), historique borné
  au dernier démarrage, filtres texte/niveau.
- Joueurs : liste unifiée (whitelist, opérateurs avec niveaux, bans
  permanents et temporisés, kick), historique de sessions et statistiques
  vanilla, avatars, fusion des identités pré-mc-admin.
- Réglages de jeu : les 58 gamerules (MC 26.1), difficulté, météo, heure.
- Redémarrage programmé ponctuel (avertissements dégressifs in-game) et
  redémarrage quotidien récurrent.
- Mise à jour contrôlée du serveur Minecraft (conteneur one-shot dédié,
  refus si joueurs connectés).

### Sauvegardes
- Profils de sauvegarde (contenu, planification, rétention par profil).
- Restauration transactionnelle : sauvegarde de sécurité vérifiée,
  rollback conservé jusqu'au retour healthy, préflight fail-closed
  (intégrité image/réseau des conteneurs outils).
- Vérification d'intégrité des archives en tâche de fond, favoris,
  téléchargement, chronologie.

### Surveillance
- Page Performances : TPS/MSPT, top des entités par monde, chunks,
  profils spark téléchargeables.
- Alertes lag et espace disque, surveillance de conteneurs compagnons.
- Notifications en profils : canaux Discord/Telegram multiples, 10
  événements filtrables par canal, assistant de configuration.
- Mods : inventaire enrichi Modrinth (identification SHA-1, badges de
  mise à jour disponibles — lecture seule, mc-admin ne modifie jamais un mod).

### Socle
- RBAC par permissions (rôles YAML, deny-by-default, côté serveur),
  audit append-only complet, journal d'opérations groupées.
- Comptes gérés dans l'UI (création, rôles, réinitialisation), premier
  lancement guidé (/setup), branchements testés en direct (page Serveurs).
- Sécurité : socket-proxy Docker à whitelist minimale, RCON jamais publié,
  Argon2, CSRF, anti-bruteforce, écritures JSON atomiques.
- Interface responsive (desktop/mobile), thèmes sombre/clair,
  accessibilité, erreurs à codes stables avec détails copiables.

### Vers le 1.0.0
Réservé à : bouton de mise à jour de mc-admin en un clic, mode
maintenance avec portier MOTD, copie des sauvegardes hors NAS.
