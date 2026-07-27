# BlueMap derrière mc-admin

BlueMap tourne dans un conteneur séparé et lit le monde en lecture seule.
Son serveur HTTP n'est jamais publié sur l'hôte : seul mc-admin peut le
joindre sur le réseau Docker dédié `mc_map_internal`, puis le relaie sous
`/map/embed/` après authentification.

Le réseau conserve une sortie vers Internet car BlueMap peut devoir télécharger
les ressources Minecraft acceptées dans sa configuration. L'absence de section
`ports` bloque néanmoins tout accès entrant direct depuis l'hôte ou le LAN.

L'image est épinglée par digest. Le digest fourni correspond à l'image
BlueMap utilisée et validée lors de la migration initiale. Une mise à jour de
BlueMap est donc une opération explicite, testée séparément.

## Nouvelle installation

1. Créer les dossiers indiqués par `BLUEMAP_CONFIG_DIR`,
   `BLUEMAP_DATA_DIR` et `BLUEMAP_WEB_DIR`.
2. Initialiser la configuration selon la documentation officielle BlueMap,
   notamment l'acceptation du téléchargement des ressources Minecraft.
3. Vérifier que `MC_WORLD_DIR` pointe vers le dossier du monde.
4. Démarrer le service :

   ```sh
   docker compose --profile map up -d bluemap
   ```

5. Dans mc-admin, page **Carte**, tester puis enregistrer
   `http://bluemap:8100`.

Le Compose ne contient aucune section `ports` pour BlueMap. Ne pas en ajouter :
un accès direct contournerait l'authentification de mc-admin.

## Migration du conteneur `bluemap-trial`

La migration réutilise les dossiers existants ; aucune copie de rendu n'est
nécessaire. Ne jamais faire tourner les deux conteneurs simultanément : ils
écriraient dans les mêmes dossiers `data` et `web`.

Avant intervention, relever l'heure de démarrage de Minecraft. Puis :

1. valider la configuration effective avec `docker compose config` ;
2. arrêter `bluemap-trial` sans le supprimer ;
3. démarrer uniquement le nouveau service `bluemap` ;
4. enregistrer `http://bluemap:8100` dans la page **Carte** ;
5. vérifier la carte desktop/mobile, les tuiles et les logs ;
6. confirmer que le port 8100 n'est plus publié et que Minecraft n'a pas
   redémarré ;
7. conserver `bluemap-trial` arrêté jusqu'à validation définitive.

## Rollback

1. arrêter le service `bluemap` ;
2. redémarrer `bluemap-trial` ;
3. remettre temporairement son ancienne adresse dans la page **Carte** ;
4. vérifier la carte et l'état inchangé de Minecraft.

Le conteneur provisoire n'est supprimé qu'après validation explicite du nouveau
service et de son rendu.
