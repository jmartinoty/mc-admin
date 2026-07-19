# Stack monitoring (copie versionnée)

Source de vérité du DÉPLOIEMENT : `/Volume1/AppData/monitoring/` (convention
un-dossier-par-stack). Cette copie versionne la config pour reproductibilité —
en cas de modification, tenir les deux en sync.

Contenu : Prometheus (rétention 30 j, port 9090 LAN, rejoint aussi `mc_playit`
pour scraper `minecraft:25585`) + cAdvisor (épinglé v0.49, les versions >= 0.50
ont retiré la factory Docker) + node_exporter + mc-monitor (joueurs/latence via
ping Minecraft, sans mod) + job `minecraft_exporter` (TPS/MSPT réels via le mod
FabricExporter côté serveur, s'appuie sur spark).
