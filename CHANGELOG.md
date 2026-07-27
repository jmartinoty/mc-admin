# Changelog

Les versions suivent [semver](https://semver.org/lang/fr/). La série 0.x est
une bêta publique : l'outil est utilisé en production chez son auteur, mais
des morceaux annoncés manquent encore (voir la roadmap dans `docs/`).

## v0.13.0 — 2026-07-27

### Tableau de bord
- La fermeture pour maintenance devient un bouton « Maintenance » dans la
  carte serveur, à côté de Redémarrer/Arrêter — la barre pleine largeur
  disparaît. Le formulaire (message, retour prévu, délai) s'ouvre en
  dialogue, comme le redémarrage.
- Mise à jour du serveur en une ligne : le badge indique la version
  disponible et le bouton rejoint la ligne version. La case « forcer même
  si des joueurs sont connectés » n'apparaît plus que dans le dialogue de
  confirmation — et seulement quand elle sert à quelque chose.
- Tuiles de mesures : le libellé « 24 h » ne se coupe plus au milieu, la
  RAM s'affiche en Gio au-delà de 1024 Mio, et toutes les tuiles gardent
  la même hauteur, avec ou sans historique.

## v0.12.1 — 2026-07-27

### Mise à jour
- Les notes de version s'affichent désormais proprement dans le dialogue
  de mise à jour (titres, listes, gras) au lieu du Markdown brut avec des
  phrases coupées en plein milieu.

## v0.12.0 — 2026-07-27

### Sécurité (nouveau)
- **Double authentification (2FA)** en self-service : activation sur la
  nouvelle page « Sécurité » avec n'importe quelle application TOTP
  (Google Authenticator, Aegis, FreeOTP…). Un code est demandé après le
  mot de passe ; la désactivation exige le mot de passe actuel.
- **Appareils connectés** : chacun voit ses sessions ouvertes (appareil,
  adresse, dernière activité) et peut les déconnecter à distance — une
  révocation survit aux redémarrages de mc-admin.
- **Jetons d'API** (administrateur) : accès scripté via la nouvelle API
  locale documentée `/api/v1`. Un jeton est lié à un rôle existant (mêmes
  permissions, même journal d'audit que l'interface), montré une seule
  fois à la création — seule son empreinte est stockée.

### Fiabilité
- **Historique des incidents** : les indisponibilités du serveur, les
  périodes de lag et les alertes disque sont désormais persistées et
  corrélées aux actions qui les entourent (page dédiée).
- **Restauration annoncée aux joueurs** : pendant qu'une restauration
  arrête le serveur, le portier de maintenance tient sa place — la liste
  des serveurs affiche « Restauration en cours » au lieu d'une connexion
  refusée. Aucun réglage si le portier n'est pas installé : la
  restauration reste simplement silencieuse, comme avant.

## v0.11.0 — 2026-07-27

### Sauvegardes
- L'assistant de profil avance désormais en étapes guidées, avec des
  questions en langage clair plutôt que des champs techniques.
- La modale de sauvegarde pose des questions simples (quoi sauvegarder,
  quand) au lieu d'exposer le vocabulaire Docker.

### Messages
- Les erreurs s'affichent en langage clair, chacune avec un code stable
  documenté ([docs/codes.md](docs/codes.md)) pour retrouver le détail.

### Mise à jour
- Correctif : la mise à jour en un clic recrée désormais **tous** les
  outils internes (dont le portier de maintenance) sur la nouvelle image —
  ils ne peuvent plus rester sur une version périmée.
- Le bouton « Me le rappeler plus tard », annoncé dans les notes de la
  v0.10.1, est réellement inclus à partir de cette version.

## v0.10.1 — 2026-07-20

### Terminal
- Pause du défilement des logs — utile pour lire tranquillement pendant un
  incident, sans que l'écran continue de défiler.
- Recherche avec surlignage des mots trouvés et compteur de résultats.
- Copier une ligne de log en un clic (icône au survol de la ligne).
- Commandes RCON favorites, séparées de l'historique : cliquer un favori
  remplit la commande sans l'envoyer, l'exécution reste toujours volontaire.

### Performances
- Le graphe MSPT marque désormais les sauvegardes, redémarrages et mises à
  jour du serveur — plus facile de relier un pic de lag à un événement.
- Seuils d'alerte réglables directement dans l'interface (MSPT, espace
  disque, durée avant que mc-admin prévienne).

### Sauvegardes
- Nouvelle étape « Destination » dans l'assistant de création de profil
  (Local, comme aujourd'hui ; Distant/Cloud en aperçu, bientôt disponible).
- Historique de stockage : courbe d'occupation par type de sauvegarde et
  estimation honnête (« saturation prévue vers telle date », ou « pas de
  souci prévisible »).

### Carte
- Déplacement plus fluide sur la carte (les tuiles se chargent plus vite).
- Distance de rendu en haute précision étendue.

### Mise à jour
- Bandeau de mise à jour compact, avec un bouton « Voir le détail » pour les
  notes complètes.
- Bouton « Me le rappeler plus tard » : repousse le bandeau d'un jour. Si une
  version plus récente sort entre-temps, le rappel repart quand même.

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
