# Patch notes

Liste courte, par ordre antéchronologique, de chaque correctif — distincte du
`CHANGELOG.md` (qui suit semver, par release). Une entrée par correctif :
date, une ligne de constat, une ligne de cause/fix. Mise à jour à chaque bug
fix, même hors release.

## 2026-11-02

- **Changer un mot de passe ne déconnectait personne** — relevé par la
  revue du 02/11. Ni la réinitialisation par l'owner ni le changement de
  son propre mot de passe ne touchaient aux sessions ouvertes : un compte
  dont le mot de passe avait fuité restait utilisable 14 jours par celui
  qui l'avait. Fix : la réinitialisation par l'owner déconnecte tous les
  appareils du compte (le nombre est journalisé) ; changer son propre mot
  de passe déconnecte ses autres appareils, pas celui d'où l'on agit.
- **Un compte supprimé puis recréé héritait de l'ancienne 2FA et des
  anciennes sessions** — relevé par la revue du 02/11. La suppression
  n'effaçait que le compte et son mot de passe : le secret de double
  authentification (`totp.json`) et les sessions (`sessions.json`,
  14 jours) restaient. Recréé sous le même identifiant, le compte exigeait
  le code de l'ANCIEN téléphone et un ancien cookie redevenait valide — et
  l'owner n'avait aucun moyen d'aider un ami ayant perdu son téléphone.
  Fix : la suppression efface 2FA et sessions, la création purge les
  restes éventuels, et la page Comptes propose « Réinitialiser la 2FA »
  (owner, audité, jamais sur son propre compte).
- **Pendant une restauration, « Rouvrir » aurait démarré Minecraft sur un
  monde à moitié remplacé** — trouvé par la revue du 02/11, jamais
  constaté (aucune restauration depuis la V7b). Depuis la V7b, c'est le
  portier de la restauration qui tient l'adresse pendant le remplacement
  du monde : l'accueil y voyait une maintenance et proposait « Rouvrir »
  aux owners et admins. À l'inverse, une restauration lancée pendant une
  maintenance la levait en silence à la fin, et une fermeture annoncée
  pouvait arrêter le serveur en pleine restauration. Fix : les deux
  s'excluent — restauration refusée pendant une maintenance active ou
  annoncée ; « Fermer » et « Rouvrir » refusés pendant une restauration ;
  une fermeture déjà annoncée attend la fin de la restauration ; l'accueil
  affiche « Serveur fermé pendant une restauration », sans bouton.
- **Une maintenance pouvait fermer le serveur pour rien** — relevé par la
  revue du 02/11. Le contrôle d'alignement du portier (image, réseaux)
  était déclaré pour la maintenance mais jamais appelé : un portier périmé
  ne se découvrait qu'APRÈS l'arrêt de Minecraft, laissant les joueurs
  devant un « connexion refusée ». Fix : contrôle avant tout arrêt — à la
  demande, à l'armement d'une maintenance programmée et à l'échéance d'une
  fermeture différée (annulée et annoncée en jeu si le portier s'est
  périmé entre-temps). Un état invérifiable ne bloque pas.
- **Chaque onglet Carte fermé pouvait bloquer l'application** — relevé
  par la revue du 02/11, jamais constaté en usage. Le relais du flux des
  joueurs de BlueMap lisait sans délai d'attente, dans un thread du pool
  de l'application (40 en tout, partagés avec toutes les pages), et un
  thread bloqué en lecture n'est pas libéré quand le navigateur se
  déconnecte. BlueMap n'émettant rien tant que personne ne bouge (mesuré :
  aucune ligne en 150 s, serveur vide), un onglet fermé gardait son thread
  jusqu'au prochain mouvement de joueur ; 40 fermetures suffisaient à figer
  l'interface. Fix : après 30 s de silence, le flux se termine proprement ;
  BlueMap repasse brièvement en interrogation périodique puis se reconnecte
  de lui-même.


## 2026-08-24

- **Les sparklines « Chunks chargés » s'affichaient en aplat noir** —
  relevé par une revue de la page Performances, présent en prod depuis la
  V6.7. Les règles CSS des sparklines étaient écrites pour les tuiles
  (`.metric .spark`) ; dans le bloc par monde (`.perf-world`), AUCUNE ne
  s'appliquait et la polyline prenait le rendu SVG par défaut : remplissage
  noir, pas de trait. Fix : règles dédiées `.perf-world .spark`.


## 2026-08-23

- **Le graphe MSPT décalait sa courbe dès qu'une mesure manquait** —
  trouvé en préparant le défilement dans le temps, jamais signalé par
  Jeremy : le défaut est invisible tant que la série est complète. Les
  points étaient placés à leur RANG (`span / (len - 1)`) alors que les
  graduations horaires et les repères de sauvegarde étaient placés à leur
  INSTANT. Prometheus n'émettant aucun point quand il n'a pas mesuré, un
  trou se contractait à zéro pixel et tout son passé glissait vers la
  droite. Mesuré sur les données réelles du NAS : un trou contigu de
  12,0 h le 31/07 (710 points au lieu de 721 sur 30 jours) décalait la
  courbe de près de 6 h sur une fenêtre de 24 h — les pics de MSPT
  n'étaient plus en face des sauvegardes qui les causaient. Fix :
  `mspt_history` porte des couples (instant, valeur) et la vue place les
  points sur l'axe du temps ; un trou COUPE désormais la courbe au lieu
  d'être enjambé par un trait droit qui ferait croire à des mesures
  inexistantes, et la légende l'annonce. Les sparklines gardent des
  valeurs nues : sans axe ni repère, un trou y est sans conséquence.


## 2026-07-31

- **La version du jeu ne revenait qu'au rechargement manuel de la page** —
  constaté par Jeremy après un redémarrage de la prod. Pendant l'arrêt,
  mc-monitor n'expose plus de mesure : la version passe à « ? », et la
  ligne n'était PAS dans le fragment pollé (exclue par crainte de
  marteler l'API Mojang). Or le check est déjà mis en cache 60 s côté
  adapter : la version rejoint donc la zone rafraîchie et se rétablit
  seule. Le BOUTON de mise à jour reste hors du fragment — on ne
  remplace jamais un formulaire sous le doigt de l'utilisateur.


- **« Démarrer » pendant une maintenance → « Internal Server Error »** —
  constaté par Jeremy en prod. Le portier occupe l'adresse STATIQUE du
  serveur : Docker refuse le démarrage (`Address already in use`), et
  l'exception docker-py brute n'étant pas une erreur métier, elle
  échappait au filet de la route et sortait en 500. Double fix : (1)
  `start()`/`restart()` refusent net pendant une maintenance, en indiquant
  le geste correct (« Rouvrir le serveur »), refus audité ; (2) toute
  action mutante de l'adapter Docker traduit désormais l'échec en erreur
  métier avec l'explication lisible (« Address already in use ») au lieu
  du pavé HTTP — plus aucune 500 possible sur start/stop/restart.


- **MOTD de maintenance redondant : « ⚙ Maintenance en cours » suivi de
  « Le serveur est fermé pour maintenance. »** — constaté par Jeremy en
  test réel du portier sur la prod. Le message par défaut remplissait la
  2ᵉ ligne même quand elle n'ajoutait rien, répétant le titre. Fix : la
  2ᵉ ligne ne porte plus que ce qui informe (message personnalisé et/ou
  retour prévu) ; sans rien à dire, le MOTD tient sur la seule ligne de
  titre. Le refus au login garde une phrase complète (le joueur n'a pas
  le titre sous les yeux).

## 2026-07-27

- **Tuiles : la sparkline passait par-dessus le libellé (RAM/TPS) et
  « Joueurs en ligne » était tronqué en « Joueurs en li… »** — constaté
  par Jeremy sur capture (0.13.0). La sparkline était positionnée en
  absolu dans la tuile : dès que la courbe montait, elle traversait le
  texte ; et le nowrap+ellipsis de la veille coupait le nom. Fix :
  colonne flex, sparkline en flux poussée en bas, nom jamais tronqué,
  hint « · 24 h » atomique (`bbe5d35`).

- **Tuile « Joueurs en ligne » : le libellé « 24 h » se coupait (« 24 »
  sur une ligne, « h » orpheline dessous)** — constaté par Jeremy sur
  capture. Le hint est désormais insécable dans un libellé flex
  (`metric-label`), et les tuiles sans historique reçoivent une ligne de
  base neutre pour garder la même hauteur (`890137b`).

- **Notes de version illisibles dans le dialogue de mise à jour**
  (constaté par Jeremy sur la carte 0.12.0). Le Markdown brut de l'API
  GitHub était affiché tel quel : `###`, `**` et retours à la ligne du
  fichier source coupaient les phrases. Fix : mini-rendu stdlib
  (`api/release_notes.py`) — titres/listes/gras/liens, texte échappé
  d'abord, lignes recollées (`5a7539a`).
- **Mise à jour en un clic : mc-doorman (et mc-op-levels dans le compose
  exemple) restaient sur l'ancienne image après une mise à jour.** La
  commande du one-shot `mc-admin-updater` ne recréait pas tous les
  one-shots basés sur l'image mc-admin — exactement le piège des « workers
  périmés » que ce mécanisme devait éliminer. Fix : liste de recréation
  complétée dans les deux composes.

## 2026-07-20

- **Page d'accueil en erreur 500 quand `PROMETHEUS_URL` est vide.** Constaté
  en labo Docker (install fraîche, Prometheus facultatif non configuré) :
  `urllib.request.urlopen` lève `ValueError` (« unknown url type ») sur une
  URL sans schéma, non capturée par `PrometheusMetrics._query_value`/
  `_query_vector` (seules les erreurs réseau l'étaient) — remontait en 500
  brut au lieu du panneau « indisponible » déjà prévu. Fix : `ValueError`
  ajoutée aux exceptions qui dégradent en `ServerUnavailable`
  (`app/adapters/prometheus.py`).
- **BlueMap : modélisation précise perdue ~100 blocs après un bâtiment
  chargé.** Réglage `hires-slider-default` de BlueMap à 100 (défaut du
  logiciel) — bascule en basse résolution dès qu'on s'éloigne. Remonté à 300
  (webapp.conf, `bluemap-trial`), réglable jusqu'à 500 dans le menu de la
  carte. Config, pas de code — pas de redéploiement mc-admin.
- **Carte BlueMap lente/« décachée » en se déplaçant.** Le relais
  n'annonçait pas la compression à BlueMap (tuiles hires transférées ×15 plus
  lourdes que nécessaire) et les tuiles absentes (cas normal en bordure de
  rendu) repartaient sans cache, donc re-demandées à chaque mouvement. Fix :
  gzip négocié de bout en bout + cache court sur les 404 (`41d51ec`).

## 2026-08-22 — Joueur affiché en ligne pendant plus d'un jour

**Constat (Jeremy)** : mc-admin annonçait Trappeur et Mineur
connectés alors qu'un seul joueur l'était.

**Cause** : le tailer de `latest.log` mémorise sa position et fait `seek()` à
chaque passe, sans vérifier que le fichier est toujours le même. Minecraft
archive son journal chaque nuit et à chaque redémarrage, puis repart d'un
fichier vide : on cherchait à lire à l'octet 96 000 d'un fichier qui n'en
faisait plus que 5 000. Aucune ligne n'était lue et la position ne bougeait
pas — le tailer restait aveugle jusqu'à ce que le nouveau fichier dépasse
l'ancienne taille. Le « left the game » de Trappeur, à 00:33 juste
après la rotation de minuit, est tombé dans ce trou.

**Correctif** : un fichier plus court que la position mémorisée ne peut
signifier qu'une rotation ou une troncature — on repart alors du début.
Deux tests de non-régression, vérifiés en échec sans le correctif.

**Portée** : le bug datait de la V3.4 (connexions et commandes). Depuis la
0.17.0, morts, succès et tchat passent par le même tailer : chaque rotation
en perdait aussi.

**Nettoyage** : la session restée ouverte a été refermée à son heure réelle de
départ (00:33:28, relevée dans l'archive), pas à l'instant du nettoyage.
