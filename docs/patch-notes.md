# Patch notes

Liste courte, par ordre antéchronologique, de chaque correctif — distincte du
`CHANGELOG.md` (qui suit semver, par release). Une entrée par correctif :
date, une ligne de constat, une ligne de cause/fix. Mise à jour à chaque bug
fix, même hors release.

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
