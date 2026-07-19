# Feuille de route UX

Ce document garde la trace des choix d'interface convenus avec Jeremy. Il
complète `CLAUDE.md` : les règles de sécurité, d'architecture et de déploiement
restent autoritaires.

## Principes

- Une opération longue doit rester visible quand l'utilisateur change de page.
- L'interface s'adapte aux permissions, jamais au nom d'un rôle.
- Une action en cours doit être impossible à envoyer deux fois.
- Les pages classiques et les fragments HTML restent la base : pas de SPA.
- Un lien direct doit toujours exister pour les contenus ouverts en panneau.
- La couleur complète un libellé explicite ; elle ne porte jamais seule le sens.
- Les composants doivent fonctionner au clavier et avec un lecteur d'écran.

## Lot UX 2026-07

| ID | Proposition | Résultat attendu | État |
| --- | --- | --- | --- |
| UX-1 | Centre d'activité global | Le bandeau sauvegarde/restauration suit l'utilisateur sur toutes les pages autorisées. Les redémarrages programmés y sont également visibles. | Implémenté — validation navigateur en attente |
| UX-2 | États d'interface uniformes | Boutons bloqués après envoi, formulaires marqués occupés, retours accessibles et perte de connexion explicite. | Implémenté — validation navigateur en attente |
| UX-3 | Accueil adapté aux permissions | Un utilisateur de consultation voit l'essentiel ; les outils techniques restent réservés aux permissions d'administration. | Implémenté — validation navigateur en attente |
| UX-5 | Fiche joueur en panneau latéral | Les statistiques s'ouvrent depuis la liste sans perdre le filtre, avec un lien direct vers la page complète. | Implémenté — validation navigateur en attente |
| UX-6 | Chronologie des sauvegardes | Les archives sont regroupées par jour, lisibles sur mobile, avec type, intégrité, taille et actions. | Implémenté — validation navigateur en attente |
| UX-11 | Accessibilité | Navigation clavier, focus visible, titres de page, dialogues nommés, zones live, réduction des animations et structure HTML valide. | Implémenté — validation navigateur en attente |

## Contrats par proposition

### UX-1 - Centre d'activité

Le bandeau existant reste la référence visuelle :

- bleu pour une sauvegarde ;
- orange pour une restauration ;
- vert pour une réussite persistante ;
- rouge pour un échec persistant ;
- bleu avec progression indéterminée pour un redémarrage programmé.

L'état vient exclusivement du serveur. Le navigateur interroge un endpoint
global et ne reconstruit pas un état depuis `localStorage`. Une opération
active est prioritaire sur un résultat passé, puis sur un redémarrage
programmé. La fermeture d'un résultat respecte la page courante.

### UX-2 - États uniformes

- Un formulaire valide passe à `aria-busy="true"` et désactive ses boutons
  d'envoi jusqu'à la navigation.
- Les confirmations intégrées utilisent la même mécanique.
- Les messages sont annoncés dans une zone `aria-live` et restent assez
  longtemps pour être lus.
- Les pollings signalent une perte de connexion après plusieurs échecs, puis
  annoncent le retour à la normale.
- Une erreur n'efface pas les données déjà visibles.

### UX-3 - Accueil adapté

Le mode est calculé avec les permissions :

- **consultation** : état du serveur, adresse, joueurs, métriques principales
  et logs si autorisés ;
- **opérations** : commandes de cycle de vie, mises à jour, réglages et détails
  d'infrastructure selon chaque permission.

Le nom du rôle (`owner`, `admin`, `friend`, etc.) ne conditionne jamais le
rendu ni l'autorisation.

### UX-5 - Panneau joueur

- Le clic sur un joueur ouvre un `dialog` latéral.
- Le chargement affiche un état explicite et conserve la liste derrière.
- Le panneau montre les mêmes données fiables que la page complète.
- Le lien reste navigable normalement avec ouverture dans un nouvel onglet,
  clavier modifié ou JavaScript indisponible.
- La fermeture rend le focus au joueur d'origine.

### UX-6 - Chronologie des archives

- Tri décroissant, puis groupes « Aujourd'hui », « Hier » et date complète.
- Chaque archive affiche son heure, son profil/type, son intégrité, sa taille
  et les actions permises.
- Les filtres existants et le verrou pendant une opération sont conservés.
- L'état vide précise s'il concerne toutes les archives ou seulement le filtre.

### UX-11 - Accessibilité

Critères minimum :

- un seul élément `main` et un lien d'évitement ;
- `aria-current` dans la navigation ;
- titre de document propre à chaque page ;
- focus visible cohérent et retour du focus après un dialogue ;
- dialogues reliés à un titre et, si utile, à une description ;
- boutons icônes nommés ;
- onglets/filtres exposant leur état ;
- animations neutralisées avec `prefers-reduced-motion`;
- vérification desktop et mobile dans un navigateur réel.

## Hors lot

- Un historique général des opérations n'est pas ajouté au bandeau : le
  Journal reste la source d'historique.
- Une progression de mise à jour serveur ne sera affichée que lorsqu'une source
  d'état métier fiable existera.
- Ce lot ne change aucune permission et ne redémarre pas Minecraft.

## Validation

- Tests HTTP ciblés pour les permissions, le centre d'activité, le panneau
  joueur et les groupes d'archives.
- `ruff check app tests`, `pytest` complet et `git diff --check`.
- Parcours navigateur desktop et mobile, avec captures de contrôle.
- Déploiement du seul service `mc-admin`, puis contrôle de santé de
  `mc-admin`, de `minecraft` et de l'URL HTTPS.
