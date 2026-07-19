# Changelog

Les versions suivent [semver](https://semver.org/lang/fr/). La série 0.x est
une bêta publique : l'outil est utilisé en production chez son auteur, mais
des morceaux annoncés manquent encore (voir la roadmap dans `docs/`).

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
