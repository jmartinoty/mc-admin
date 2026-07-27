# Comprendre les messages de mc-admin

Quand quelque chose ne va pas, mc-admin affiche un **message court** en langage
clair, suivi d'un **petit code** (par exemple `JEU-90`). Ce guide explique
chaque code : ce qu'il veut dire, la cause probable, et quoi vérifier.

Les codes sont stables : ils ne changent pas d'une version à l'autre, tu peux
donc les citer si tu demandes de l'aide.

Deux familles de codes :

- **États du moment** (`-90`, `-91`…) : quelque chose est momentanément
  indisponible (souvent le serveur qui démarre ou une brique de supervision
  injoignable). En général, ça se résout tout seul.
- **Erreurs d'action** (`-01`, `-02`…) : une action précise a échoué. Le
  message « Détails » de mc-admin contient le texte technique complet.

---

## États du moment

### JEU-90

**« Le jeu ne répond pas. »**

mc-admin parle au serveur Minecraft pour lire les joueurs, la liste blanche,
les opérateurs, les bannis ou les réglages. Ce message apparaît quand le
serveur ne répond pas encore.

Causes probables :

- le serveur vient de démarrer et n'est pas tout à fait prêt (patiente une
  minute) ;
- le serveur est arrêté ou en cours de redémarrage ;
- la communication avec le jeu est mal configurée (page **Serveurs**).

### SRV-90

**« mc-admin n'arrive pas à joindre le serveur. »**

mc-admin ne trouve pas du tout le serveur de jeu.

Causes probables :

- le serveur n'est pas démarré ;
- son adresse est mal renseignée dans la page **Serveurs** — vérifie-la puis
  recharge.

### SUR-90

**« Mesures indisponibles. »**

Les graphiques de performance (joueurs, TPS, MSPT, mémoire, entités…) viennent
d'un outil de supervision séparé. Ce message apparaît quand cet outil est
injoignable.

Causes probables :

- l'outil de supervision est arrêté ou pas encore prêt ;
- installation sans supervision (les graphiques restent alors indisponibles).

Le reste de mc-admin continue de fonctionner normalement.

### MAJ-90

**« Version du jeu indisponible. »**

mc-admin n'a pas pu déterminer la version du serveur ou la dernière version
publiée de Minecraft. C'est purement informatif — ça n'empêche rien.

Causes probables :

- pas d'accès Internet au moment de la vérification ;
- l'outil de supervision qui rapporte la version en cours est injoignable
  (voir **SUR-90**).

---

## Erreurs d'action

Ces codes accompagnent l'échec d'une action précise. Le bouton **« Détails »**
du message affiche le texte technique complet, utile pour diagnostiquer ou
demander de l'aide.

- **BKP-nn** — Sauvegardes (déclenchement, téléchargement, rétention).
- **RST-nn** — Restauration d'une sauvegarde.
- **JEU-nn** — Réglages du jeu (difficulté, règles, météo, heure).
- **JOU-nn** — Joueurs (liste blanche, opérateurs, bannissements, exclusions).
- **SRV-nn** — Serveurs et carte du monde.
- **SUR-nn** — Surveillance et canaux de notification.
- **NOT-nn** — Notifications (Discord, Telegram).
- **PRF-nn** — Performances et profils d'analyse.
- **MAJ-nn** — Mise à jour de mc-admin.

Si un code n'est pas encore détaillé ici, son message et son dialogue
« Détails » dans l'application donnent déjà l'essentiel. Ce guide s'étoffe au
fil des versions.
