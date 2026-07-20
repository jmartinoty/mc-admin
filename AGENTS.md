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

---

## Note de reprise (Claude → Claude, 20/07/2026) — portier mc-doorman

Le lot « mode maintenance + portier `mc-doorman` » a été produit par **deux
sessions différentes**. Le commit `3b410ea` porte le tout sous une seule
paternité : c'est inexact, et cette note est la correction (Jeremy a
explicitement préféré documenter ici plutôt que réécrire l'historique poussé).

**Ce qui vient de la session interactive du 19/07 au soir** (22h04 → 23h27,
interrompue net par la limite de quota, travail laissé NON COMMITÉ dans le
worktree `claude/doorman`) :

- `app/doorman_server.py` — le portier protocole Minecraft (stdlib) ;
- `app/adapters/doorman.py` — `DockerDoorman` / `NotConfiguredDoorman` ;
- le domaine : `MaintenanceUnavailable`, `Permission.MAINTENANCE`,
  `PendingMaintenance`, `MaintenanceStatus`, `DoormanPort` ;
- `tests/test_doorman_server.py` + `tests/test_doorman_adapter.py` (19 tests).

**Ce qui vient de la tâche planifiée du 20/07 à 02h08** (reprise du worktree
en plan) : tout le câblage — `domain/services/maintenance.py`,
`adapters/maintenance_state.py`, `maintenance_scheduler.py`, `config.py`,
lifespan, routes + template, `ContainerPort.stop(timeout=)`, les deux
composes, la doc, et `tests/test_maintenance.py` (30 tests).

**Origine du besoin** : c'est une demande de Jeremy (session du 19/07 au
matin, « pendant une maintenance éviter que des gens se connectent, et encore
mieux avec un message »), inscrite en roadmap puis lancée sur son « attaque
oui » du soir. Ce n'est pas une initiative d'agent.

### Deux règles que cet épisode a values

1. **Vérifier l'ORIGINE d'un travail non commité avant de le reprendre.** Un
   worktree en plan n'est pas forcément le tien : les transcripts de session
   (`~/.claude/projects/<projet>/*.jsonl`) disent en 30 secondes quelle
   session a écrit quels fichiers, et surtout ce que Jeremy y a dit. Reprendre
   sans lire, c'est risquer de contredire une décision prise juste avant.
2. **Noter la paternité dans le commit** quand on committe le travail d'une
   autre session ou d'un autre agent (§0), comme l'a fait `5d4580a` pour le
   diff de Codex. `3b410ea` ne l'a pas fait — d'où cette note.
