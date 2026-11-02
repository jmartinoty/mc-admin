# Changelog

Les versions suivent [semver](https://semver.org/lang/fr/). La série 0.x est
une bêta publique : l'outil est utilisé en production chez son auteur, mais
des morceaux annoncés manquent encore (voir la roadmap dans `docs/`).

## v0.22.1 — 2026-11-02

Aucune modification du Compose. **Rouvrir le serveur avant d'appliquer
cette mise à jour s'il est en maintenance** : la version précédente ne le
vérifiait pas encore.

### Corrections

- **Quitter la page Carte laissait une connexion ouverte vers la carte.**
  Pendant qu'un joueur est en ligne, la carte envoie des nouvelles en
  continu ; une fois la page quittée, la connexion restait ouverte sans
  personne pour la lire, une dizaine de minutes. Elle est désormais fermée
  dès que la page se ferme.
- **La mise à jour de l'application est refusée pendant une maintenance.**
  Elle relance aussi le portier : en pleine maintenance, le serveur serait
  resté fermé sans plus personne pour répondre aux joueurs.

## v0.22.0 — 2026-11-02

Aucune modification du Compose : la mise à jour s'applique depuis le bouton
habituel.

### Performances

- **Le graphe de lag se parcourt dans le temps.** Il se tire à la souris (ou
  au doigt), se zoome à la molette et au clavier, et un bouton ramène à
  l'instant présent. Les 30 jours d'historique conservés sont désormais
  accessibles, et plus seulement les 7 derniers.
- **La page répond au « pourquoi ».** La courbe des joueurs connectés se
  superpose à celle du lag, sur le même axe de temps. Les ralentissements
  enregistrés sont listés sous le graphe : un clic centre la courbe sur
  l'épisode.
- Les tuiles affichent la part du budget d'un tick consommée (par exemple
  « 6 % »), les millisecondes restant visibles à côté.
- Le verdict « sous le seuil / seuil dépassé » décrit la période affichée,
  et non plus celle d'avant le déplacement.
- **Les mesures sont placées à leur heure réelle.** Une mesure manquante
  faisait glisser toute la courbe : un trou de 12 h décalait les pics de
  près de 6 h par rapport aux sauvegardes qui les causaient. Un trou coupe
  désormais la courbe au lieu d'être enjambé.
- Les mini-courbes par monde s'affichaient en aplat noir.

### Accueil et menu

- Chaque tuile de mesure de l'accueil mène à sa page de détail.
- Le menu est rangé en trois rubriques : **Jouer**, **Protéger** et
  **Administrer**. Aucune rubrique ne s'affiche vide pour un compte aux
  droits réduits.

### Fiabilité et sécurité

- **Pendant une restauration, l'accueil proposait « Rouvrir le serveur ».**
  Le portier tient l'adresse du serveur pendant le remplacement du monde, et
  l'accueil prenait cela pour une maintenance : un clic aurait démarré
  Minecraft sur un monde à moitié remplacé. L'accueil affiche désormais
  « Serveur fermé pendant une restauration », sans bouton. Plus largement,
  restauration et maintenance ne peuvent plus se chevaucher : une
  restauration est refusée pendant une maintenance active ou annoncée, et
  une fermeture annoncée attend la fin d'une restauration en cours.
- **Une maintenance ne ferme plus le serveur pour rien.** Le portier est
  vérifié avant l'arrêt du serveur : s'il n'est pas à jour, la maintenance
  est refusée et le serveur reste ouvert. Une fermeture programmée ou
  différée est annulée, et annoncée en jeu, si le portier n'est plus apte au
  moment de fermer.
- **Changer un mot de passe déconnecte les autres appareils.** Une
  réinitialisation par l'administrateur déconnecte le compte partout ; un
  changement de son propre mot de passe déconnecte ses autres appareils.
- **Supprimer un compte efface aussi sa double authentification et ses
  sessions.** Un compte recréé sous le même nom en héritait. La page Comptes
  propose aussi « Réinitialiser la 2FA », pour aider quelqu'un qui a perdu
  son téléphone.
- La carte pouvait, à la longue, bloquer l'application : chaque onglet Carte
  fermé alors que personne ne jouait gardait une ressource réservée
  jusqu'au prochain mouvement d'un joueur. Le flux des joueurs se termine
  après 30 secondes de silence, et la carte se reconnecte d'elle-même.

### Corrections

- Trois formulations tutoyaient encore l'utilisateur. Les messages affichés
  aux joueurs dans le jeu gardent, eux, le tutoiement d'usage.

## v0.21.0 — 2026-08-23

### Carte

- **Les joueurs se déplacent enfin en direct.** BlueMap suit les joueurs par un
  flux permanent, que le relais de mc-admin interrompait au bout de dix
  secondes et retenait en mémoire tampon : les positions restaient figées
  jusqu'au rechargement de la page. Les flux sont désormais relayés au fil de
  l'eau.

### Corrections

- La croix qui ferme le bandeau d'activité ramenait à la page des Sauvegardes
  au lieu de laisser sur la page courante. Elle transmettait pourtant la bonne
  destination : c'est le contrôle de sécurité qui la rejetait, faute de
  connaître toutes les pages. Il vérifie maintenant la forme de l'adresse, ce
  qui protège autant sans jamais se périmer.
- Une vingtaine de formulations tutoyaient encore, dont le message affiché
  pendant la mise à jour de l'application.

## v0.20.0 — 2026-08-23

### Surveillance

- **Les incidents rejoignent la page Surveillance**, en deux onglets : ce qui
  est suivi en ce moment, et ce qui est tombé par le passé. Ils répondaient à
  la même question depuis deux pages différentes. Une pastille rouge signale
  les pannes en cours.
- La page s'ouvre désormais à tous les comptes : l'état des services était
  déjà visible depuis l'accueil. **Choisir ce qui est surveillé reste
  réservé** aux comptes qui gèrent le serveur.
- L'ancienne adresse des incidents redirige vers le bon onglet.

## v0.19.0 — 2026-08-23

### Double authentification

- **QR code à scanner.** L'activation demandait de recopier à la main une clé
  de 32 caractères dans son application d'authentification — pénible sur un
  téléphone, et la page laissait croire qu'un code était affiché alors qu'il
  n'y en avait jamais eu. Le QR est maintenant généré et affiché ; la clé
  reste sous lui, pour les applications qui préfèrent la saisie manuelle.

### Comptes

- Les identifiants suivent la **convention Minecraft** : 3 à 16 caractères.
  La règle ne vaut qu'à la création — un compte plus ancien et plus long
  continue de se connecter.
- Le tableau des comptes ne s'étire plus sur toute la largeur de l'écran, et
  un nom trop long est abrégé plutôt que de bousculer les colonnes voisines.

### Coulisses

- Nouvelle dépendance : `segno`, pour la génération du QR code. Écrire un
  encodeur à la main demandait de réimplémenter la norme entière — un code
  qui ressemble à un QR sans se scanner aurait été pire que pas de QR du
  tout, s'agissant de la seule porte d'entrée de la double authentification.
- Un contrôle automatique refuse désormais qu'une identité réelle de joueur
  se retrouve dans les fichiers publiés.

## v0.18.0 — 2026-08-22

### Correction importante

- **Les journaux du serveur n'étaient plus lus après leur rotation.** Minecraft
  archive `latest.log` chaque nuit et à chaque redémarrage, puis repart d'un
  fichier vide ; mc-admin continuait de chercher à lire au-delà de la fin de ce
  nouveau fichier et restait aveugle jusqu'à ce qu'il retrouve la taille de
  l'ancien — souvent plusieurs heures. Connexions, déconnexions, commandes, et
  depuis la 0.17.0 morts, succès et messages, étaient perdus pendant ce temps.
  Un joueur parti pendant ce trou restait affiché « en ligne » indéfiniment.
- Le tchat écrit par PaperMC depuis son fil dédié était ignoré, à l'analyse
  comme à l'import des anciennes archives.

### Rôles

- **Quatre échelons au lieu de trois**, avec l'apparition du **modérateur** :
  il s'occupe des joueurs — expulsion, bannissement, liste blanche, journal —
  sans pouvoir toucher au serveur. L'administrateur gagne la maintenance, les
  réglages du jeu et les opérateurs. Restent réservées au propriétaire les
  actions sans retour en arrière ou qui distribuent les droits : console brute,
  restauration, arrêt définitif, mise à jour, serveurs, comptes.
- Les rôles s'affichent en toutes lettres. Un rôle défini sur mesure garde le
  nom qu'on lui a donné : les noms de rôles restent libres.
- Les identifiants suivent désormais la convention Minecraft, 3 à 16
  caractères. La règle ne s'applique qu'à la création : les comptes existants
  plus longs continuent de fonctionner.

### Page « Mon compte »

- Le nom affiché, le mot de passe, la double authentification et les appareils
  connectés étaient répartis entre deux dépliants et deux pages. Ils sont
  réunis sur une seule page, en onglets, et le menu de profil n'en garde qu'une
  entrée. Les anciennes adresses redirigent vers la bonne section.

### Formulation

- L'interface s'adresse désormais à la troisième personne. Elle tutoyait, par
  héritage du temps où elle n'avait qu'un seul utilisateur.

### Corrections d'affichage

- Le menu de rôle de la page Comptes débordait sur la colonne voisine dès que
  les libellés s'allongeaient ; le tableau ne s'étire plus indéfiniment et un
  nom trop long est tronqué, lisible au survol.
- Les cases à cocher du Journal, ajoutées en 0.17.0, ne filtraient rien : le
  bouton « Filtrer » étant masqué, seules les listes déroulantes déclenchaient
  la recherche.

## v0.17.0 — 2026-08-22

### Suivi des faits de jeu (nouveau)

Le serveur écrivait déjà tout dans ses journaux ; mc-admin sait maintenant les
lire, les garder et les montrer.

- **Qui a tué qui.** Chaque mort est enregistrée avec sa cause et son auteur,
  et les duels entre joueurs sont distingués des créatures : « Trappeur
  a été tué par Bucheron » ne se lit pas comme « tué par un Noyé ». La page
  Joueurs gagne un **tableau des duels**.
- **Fiche joueur enrichie** : morts observées, duels gagnés, cause de mort la
  plus fréquente et **bête noire** — celle qui tue le plus, joueur ou
  créature confondus.
- **Succès et messages** rejoignent aussi le Journal, dans sa famille
  « En jeu », avec de **nouvelles cases à cocher** pour choisir ce qu'on
  affiche. Les messages sont masqués par défaut : ce sont eux qui noient le
  reste.
- **Historique des messages d'un joueur**, sur sa fiche, réservé aux comptes
  pouvant sanctionner : de quoi vérifier un signalement avant d'agir.
- Un script d'import (`scripts/import_game_events.py`) rejoue les anciennes
  archives de journaux, pour que le suivi ne commence pas à zéro. Il peut être
  relancé sans jamais dupliquer l'historique.
- Les messages écrits en jeu sont conservés sans limite de temps. Un réglage
  (`GAME_CHAT_RETENTION_DAYS`) permet de les purger au-delà d'un certain âge ;
  morts et succès, eux, ne sont jamais effacés.

### Fiche joueur

- La fiche complète s'organise en **onglets** — Vue d'ensemble, Faits de jeu,
  Messages, Sessions — au lieu d'une page à rallonge. L'onglet ouvert reste
  dans l'adresse, donc partageable.
- Le **volet latéral** donne désormais un aperçu (3 derniers messages, 3
  dernières sessions) avec le total et un lien vers la fiche complète.
- Les longues listes défilent dans leur propre cadre plutôt que d'étirer la
  page.

## v0.16.0 — 2026-08-04

### Maintenance et redémarrages
- **Nouvelle option « Ne pas exécuter tant que des joueurs sont connectés »**
  sur les redémarrages (ponctuel et quotidien) et sur les fermetures pour
  maintenance : à l'heure dite, l'opération **attend que le serveur soit
  vide** au lieu de déconnecter tout le monde. Elle se déclenche dès le
  départ du dernier joueur, et prévient une fois qu'elle patiente. Si le
  serveur ne répond pas, elle attend aussi — jamais de coupure à l'aveugle.
- **Maintenances programmées** : on peut désormais en enregistrer plusieurs,
  soit à une **date précise**, soit sur des **jours de la semaine** choisis,
  avec l'heure, le préavis et le message affiché aux joueurs. La liste se
  gère depuis le bouton Maintenance.
- Le message affiché aux joueurs ne se répète plus : « Maintenance en cours »
  n'est plus suivi d'une phrase qui redit la même chose. La seconde ligne ne
  porte que ce qui informe vraiment (votre message, l'heure de retour).

### Mises à jour du serveur
- **Vous êtes prévenu quand la mise à jour est TERMINÉE** — « Serveur mis à
  jour, 26.1 → 26.2 » — et surtout **quand elle échoue**. Jusqu'ici seul le
  lancement était annoncé : une mise à jour ratée pendant la nuit passait
  totalement inaperçue.
- Lancer une mise à jour pendant une maintenance est désormais refusé avec une
  explication : les deux se disputent l'adresse réseau du serveur, la mise à
  jour aurait échoué en laissant le serveur fermé.

### Tableau de bord et sauvegardes
- La **version du jeu se réaffiche toute seule** après un redémarrage : plus
  besoin de recharger la page pour la voir revenir.
- Plus aucune page d'erreur technique sur « Démarrer », « Arrêter » ou
  « Redémarrer » : les refus de Docker sont expliqués en clair. En
  particulier, démarrer le serveur pendant une maintenance indique
  maintenant d'utiliser « Rouvrir le serveur ».
- **Liste des sauvegardes plus lisible** : l'heure est passée à gauche, contre
  les points de la frise, et le nom de l'archive ne répète plus la date.

## v0.15.0 — 2026-07-27

### Carte BlueMap
- BlueMap dispose désormais d'un service Compose reproductible, avec une
  image épinglée, des limites de ressources et un redémarrage automatique.
- Le port 8100 n'est plus publié : la carte est accessible uniquement par le
  relais authentifié de mc-admin.
- Le monde Minecraft reste monté en lecture seule et les données BlueMap
  existantes sont réutilisées sans copie.
- Un guide couvre l'installation, la migration depuis `bluemap-trial` et le
  rollback.

## v0.14.0 — 2026-07-27

### Notifications
- Les mises à jour de mc-admin ont désormais leur propre interrupteur
  (« Mises à jour de mc-admin »), **désactivé par défaut** — fini la
  notification à chaque mise à jour de l'application. L'événement
  « Mises à jour du serveur de jeu » ne concerne plus que le jeu.
  Qui veut être prévenu coche l'interrupteur sur son canal.

### Tableau de bord
- La courbe des tuiles (RAM, TPS…) ne passe plus par-dessus le libellé :
  elle vit dans sa propre bande en bas de tuile.
- Les noms de tuiles ne sont plus tronqués (« Joueurs en li… ») : le nom
  reste entier et l'indication « 24 h » passe à la ligne si la place manque.

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
