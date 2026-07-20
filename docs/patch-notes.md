# Patch notes

Liste courte, par ordre antéchronologique, de chaque correctif — distincte du
`CHANGELOG.md` (qui suit semver, par release). Une entrée par correctif :
date, une ligne de constat, une ligne de cause/fix. Mise à jour à chaque bug
fix, même hors release.

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
