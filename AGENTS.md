# Instructions pour Codex

`CLAUDE.md` est la source de vérité commune à Claude Code et Codex pour ce
projet. Avant toute analyse, modification, commande Git ou opération sur le
TerraMaster, lire ce fichier en entier, avec une attention particulière à :

- `§0` : workflow multi-agent, validation, Git et déploiement ;
- `§6` : conventions de code ;
- `§7` : garde-fous de sécurité ;
- `§9` : architecture de production.

Ne pas maintenir ici une seconde version de ces règles : toute évolution du
workflow commun doit être faite dans `CLAUDE.md`.

---

## Note de passation (Claude → Codex, 18/07/2026)

Ta session s'est arrêtée en plein lot UX (crédits épuisés). Avec l'accord
de Jeremy, Claude a repris et terminé le chantier — rien n'a été perdu :

- Ton diff non commité (passe d'accessibilité UX-11) était complet et
  vert : commité tel quel en `5d4580a` (paternité notée dans le message),
  puis la branche `codex/ux-foundations` a été fusionnée en fast-forward
  dans `main` et déployée (procédure §0 : rebuild `--no-deps mc-admin`,
  vérifications faites, `minecraft` intact).
- Tes 11 commits du jour (JSON atomique, rate-limit login, mc-op-levels,
  lifespan) avaient déjà été relus, testés et vérifiés en production.
- Ménage fait : tes 4 worktrees `../mc-admin-*` et toutes les branches
  `codex/*` (fusionnées) ont été supprimés. Repartir d'un worktree neuf
  depuis `main` (§0).
- `docs/ux-roadmap.md` fait foi pour l'état du lot : tout est
  « Implémenté — validation navigateur en attente » (côté Jeremy).

Piège confirmé 2× en réel ce jour-là : après un rebuild de l'image, les
one-shots `mc-restore`/`mc-op-levels` restent sur l'ancienne image tant
qu'ils ne sont pas recréés (`--profile tools create --force-recreate`).
Un contrôle bloquant au préflight de restauration (comparaison d'image
mc-admin ↔ workers) est planifié — vérifier dans `CLAUDE.md` s'il est
déjà en place avant de le refaire.
