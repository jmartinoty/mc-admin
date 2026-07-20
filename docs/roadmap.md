# Roadmap produit

Propositions de Codex (18/07/2026), arbitrées par Claude le même jour et
soumises à Jeremy. Complète `docs/ux-roadmap.md` (lot UX en cours de
validation) ; les règles de `CLAUDE.md` restent autoritaires. Une entrée
n'est développée qu'après validation explicite de Jeremy.

## Chantiers terminés (18/07/2026)

- [x] **P0 Restauration** — sauvegarde de sécurité vérifiée, transaction,
  rollback, healthcheck (worker `restore_worker.py`).
- [x] **Persistance critique** — écritures atomiques, fichiers corrompus
  préservés (jamais écrasés en silence), secrets en 0600.
- [x] **Tâches de fond** — démarrage/arrêt propres et ordonnés des watchers.
- [x] **Niveaux OP** — application hors ligne transactionnelle
  (`mc-op-levels`), `ops.json` repasse en lecture seule pour mc-admin.
- [x] **Authentification** — anti-bruteforce par couple IP+pseudo,
  X-Forwarded-For borné au proxy de confiance.
- [x] **Workflow Claude/Codex** — CLAUDE.md §0 (worktrees, commits,
  déploiement).
- [x] **Lot UX 2026-07** — 6 propositions livrées (voir `ux-roadmap.md`),
  validation navigateur desktop/mobile en attente côté Jeremy.

## Prochaines étapes déjà validées par Jeremy (ordre acté)

1. ✅ Essai **BlueMap** — FAIT (nuit du 18-19/07, rendu bridé) : rendu
   validé par Jeremy, verdict perf impeccable (pire fenêtre de 15 min :
   9,5 ms, 5× sous le seuil). Page « Carte » ajoutée dans mc-admin (voir
   backlog UX ci-dessous).
2. **Release v0.9.0 (bêta publique — décision Jeremy 19/07)** : EN COURS —
   scan des secrets et de l'historique FAIT (verdict : propre ; hôte
   tailnet retiré de CLAUDE.md, fixtures pseudonymisées — reste la
   décision « historique public tel quel ou repart de v0.9.0 » avant de
   passer le dépôt en visibilité publique), version + changelog FAITS
   (APP_VERSION 0.9.0, CHANGELOG.md), tag v0.9.0 poussé → image ghcr via
   CI. Restent : captures README (Jeremy), visibilité dépôt + package
   (Jeremy). La série 0.x assume les morceaux manquants ; **le 1.0.0 est
   réservé à la checklist** : bouton MAJ + mode maintenance/portier +
   sauvegardes hors NAS (socle fiabilité et installabilité déjà ✅).
   Le multi-serveur (V7) sera le 2.0.
2bis. ✅ **Découpage de `services.py`** — FAIT (19/07) : les 2673 lignes
   deviennent le package `domain/services/` — 9 modules par thème
   (base = ServiceCore RBAC/audit, monitoring, servers, accounts,
   actions, backups, game, update, moderation), `AdminService` reste la
   façade assemblée, import public et suite de tests INCHANGÉS
   (828 verts). Le bouton MAJ et le mode maintenance arriveront chacun
   dans leur module.
3. ✅ **Mise à jour en un clic depuis l'UI** — FAIT (19/07, 868 tests) :
   AppUpdateChecker (releases GitHub, 1 passe/24 h, verdict persisté,
   release v0.9.0 créée sur GitHub comme source), carte accueil owner
   (version + notes + Appliquer confirmé), one-shot privilégié
   `mc-admin-updater` (pull + recréation de mc-admin ET des one-shots —
   fin du piège des workers périmés), garde-fous audités
   (phase=app_update_* : version connue exigée, refus si sauvegarde/
   restauration en cours, install à tag local = bouton remplacé par une
   explication — le cas du NAS de dev), page /updating qui sonde le
   retour. TESTÉ EN RÉEL au banc (instance jetable + registre local) :
   cycle complet clic → recréation sous ses pieds → healthz revenu →
   audit ; le banc a attrapé un vrai bug (port de sauvegarde absent =
   refus non audité), corrigé + test de non-régression.
4. **V7 = 2.0** : V7.1 multi-serveur, V7.2 mode hôte sans Docker
   (le découpage V7.0 est remonté en 2bis).

## Backlog fiabilité (ordre recommandé)

| # | Amélioration | Intérêt | Effort | État |
| --- | --- | --- | --- | --- |
| 1 | Check d'image ET de réseau des one-shots au préflight | Refus bloquant (fail-closed pour la restauration) + diagnostic page Serveurs | Petit | ✅ Livré 18/07 |
| 2 | Journal d'audit résistant aux lignes corrompues | Ligne illisible ignorée et comptée, la page s'affiche toujours | Petit | ✅ Livré 18/07 |
| 3 | Sauvegardes hors NAS | Copie automatique (autre NAS ou S3) via conteneur rclone one-shot dédié — le rollback ne protège pas d'une panne physique du TerraMaster | Moyen | À faire |
| 4 | Seuils d'alertes configurables | MSPT, espace disque, durée d'incident réglables dans l'UI | Moyen | À faire |
| 5 | Diagnostic global | Une page synthétise ce qui ne va pas : RCON, disque, backups, notifications, proxy, workers | Moyen | À faire |
| 6 | Historique de stockage | Courbes de taille des sauvegardes, estimation du temps avant saturation | Moyen | À faire |
| 7 | Mode maintenance + portier `mc-doorman` (design validé Jeremy 19/07) | Mini-serveur stdlib répondant au protocole MC : MOTD « Maintenance — retour HH:MM » dans la liste des serveurs + refus de connexion avec message. Bouton owner (message/horizon/délai de grâce, say + compte à rebours, audité/notifié). | Moyen | ✅ Livré 20/07 (mode manuel) — voir ci-dessous |
| 7b | Portier AUTOMATIQUE pendant une restauration | Le worker `restore_worker.py` démarre/arrête le portier autour de son arrêt de minecraft. **Volontairement séparé du lot 7** : le worker a 8 points d'arrêt/démarrage (chemins de rollback et de reprise compris) et vient d'être validé en réel — l'instrumenter demande sa propre itération et son propre test de restauration, pas un ajout opportuniste | Moyen | À faire |
| 8 | Sessions utilisateur | Voir/révoquer les appareils connectés (contrainte : sessions en mémoire, process unique) | Moyen | À faire |
| 9 | 2FA pour l'owner | Protection des actions sensibles (TOTP) | Moyen | À faire |
| 10 | API locale documentée | Intégration nas-dashboard, scripts | Moyen | À faire |
| 11 | Historique des incidents | Panne + récupération + actions + durée, regroupés | Moyen | À faire |
| 12 | BlueMap propre en conteneur (décision Jeremy 19/07 : conteneur isolé, pas de mod pour l'instant) | Sortir `bluemap-trial` du provisoire : service dans un compose (redémarre avec la stack), réseau partagé avec mc-admin (adresse interne stable, plus d'IP LAN), port 8100 NON publié (la carte n'est joignable QUE via le relais authentifié), rendu planifié la nuit (bridé) | Moyen | À faire |
| — | BlueMap en mod dans le serveur (carte en continu + joueurs en direct) | PRÉREQUIS posé par Jeremy (19/07) : un monitoring de perf correct d'abord, pour mesurer l'impact du mod en connaissance de cause (CPU/RAM par conteneur dont bluemap, RAM JVM, graphiques annotés — cf. backlog n° 4/7 UX). Le mod ajoute RAM/CPU dans la JVM du jeu et son premier rendu complet tourne dedans | Moyen | Reporté |
| — | Test de restauration automatique périodique | Prérequis LEVÉ : test réel du worker réussi la nuit du 18-19/07 (protocole gamerule, exit 0, marqueur restauré). À décider plus tard si la version périodique vaut sa machinerie | Grand | Reporté |
| — | Assistant de mise à jour des mods | Écarté : contredit la doctrine « mc-admin ne met jamais un mod à jour » (badges Modrinth en lecture seule suffisent) | Grand | Écarté |

## Backlog UX (après validation du lot 2026-07)

| # | Proposition | Résultat attendu | Effort | État |
| --- | --- | --- | --- | --- |
| — | Centre d'activité, états uniformes, accueil par permissions, panneau joueur, chronologie, accessibilité | Voir `ux-roadmap.md` | — | ✅ Validé par Jeremy le 18/07 (desktop ✓, mobile fonctionnel — retours nav ci-dessous) |
| 2e | Menu mobile replié (☰) | Marque + ☰ sous 900px, menu déplié en liste verticale, utilisateur rangé dedans | Petit | ✅ Livré 18/07 |
| 1 | Erreurs lisibles | Message court + code (BKP/RST/JOU/SRV/JEU/SUR/NOT/PRF-nn) + dialogue « Détails » copiable — 33 sites convertis | Petit | ✅ Livré 18/07 |
| 2 | Joueurs pré-mc-admin : fusion et honnêteté | Alias d'époque (legacy-player-names.json, usercache prime), doublons fusionnés par pseudo, « avant mc-admin · date » estimée par mtime des stats | Petit | ✅ Livré 18/07 |
| 2b | Profils de sauvegarde : ne bloquer que le profil actif | Seul le profil dont la sauvegarde tourne est verrouillé (identité inconnue = prudence) | Petit | ✅ Livré 18/07 |
| 2c | Largeurs de colonnes stables (listes joueurs) | table-layout fixe, le pseudo absorbe l'espace | Petit | ✅ Livré 18/07 |
| 2d | État « démarrage en cours » honnête | Conteneur running + RCON muet ⇒ « démarrage en cours » (< 5 min) puis « en ligne · jeu injoignable » ; badge version en attente | Petit | ✅ Livré 18/07 |
| 2f | Têtes de skin en avatars | mc-heads.net par uuid (repli pseudo puis lettre via onerror) — liste, panneau, fiche | Petit | ✅ Livré 18/07 |
| 3 | Recherche et tri des joueurs | Recherche par pseudo, tri en ligne / dernière connexion / temps de jeu / A→Z, combinés aux filtres | Petit | ✅ Livré 19/07 |
| 3b | Page Carte (BlueMap/Dynmap) — v2 « proxy + assistant » (retour Jeremy : pas un champ dans Serveurs, et le contenu mixte HTTP/HTTPS bloquait l'iframe) | mc-admin sert la carte sous sa propre origine (/map/embed/, relais borné anti-SSRF — l'adresse peut rester interne, ex. http://minecraft:8100) ; page Carte owner = assistant (détection du mod, bouton Tester, activation) ; entrée visible par tous une fois activée | Moyen | ✅ Livré 19/07 (v2) |
| 3c | Graphe MSPT honnête | max_over_time (un pic ne se cache plus entre 2 points), axe horaire, plages 24 h/3 j/7 j, timeout dédié aux query_range | Petit | ✅ Livré 19/07 |
| 4 | Favoris et protection d'archives | Épingler une sauvegarde, l'exclure explicitement de la rétention | Moyen | À faire |
| 5 | Navigation mobile dédiée (étape 2) | Barre inférieure compacte façon app native (Accueil · Joueurs · Sauvegardes · ⋯) — après retours d'usage sur le menu replié 2e | Moyen | À faire |
| 6 | Terminal amélioré | Pause du défilement, recherche, copie, commandes favorites | Moyen | À faire |
| 7 | Graphiques annotés | Sauvegardes/redémarrages/màj marqués sur les courbes de performances | Moyen | À faire |
| 8 | Palette Cmd/Ctrl+K | Accès rapide à une page, un joueur, une action autorisée | Moyen | À faire |
