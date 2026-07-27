# Patch notes

Liste courte, par ordre antéchronologique, de chaque correctif — distincte du
`CHANGELOG.md` (qui suit semver, par release). Une entrée par correctif :
date, une ligne de constat, une ligne de cause/fix. Mise à jour à chaque bug
fix, même hors release.

## 2026-07-27

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
