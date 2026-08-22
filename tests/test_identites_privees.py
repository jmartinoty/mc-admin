"""Aucune identité réelle dans les fichiers versionnés.

Deux fuites ont eu lieu : un vrai UUID Minecraft resté dans une fixture, et
des pseudos de joueurs employés en exemple dans des notes de version. Ce test
les aurait attrapées avant publication.

LA LISTE DES IDENTITÉS N'EST PAS DANS CE FICHIER — l'y écrire reviendrait à
publier ce qu'on cherche à protéger. Elle vit dans `.identites-privees` à la
racine, non versionné (cf. .gitignore) : une entrée par ligne, les lignes
vides et celles commençant par # étant ignorées.

Sans ce fichier — sur une machine tierce, dans la CI publique — le test est
ignoré. Il protège donc là où se prennent les décisions de publication, sans
rien exiger d'ailleurs.
"""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
LISTE = RACINE / ".identites-privees"

# Ces fichiers PARLENT du sujet sans porter d'identité : les exclure évite que
# le garde-fou se déclenche sur sa propre documentation.
EXCLUS = {"tests/test_identites_privees.py", ".gitignore"}


def identites() -> list[str]:
    lignes = LISTE.read_text(encoding="utf-8").splitlines()
    return [ligne.strip() for ligne in lignes
            if ligne.strip() and not ligne.strip().startswith("#")]


def fichiers_versionnes() -> list[str]:
    sortie = subprocess.run(["git", "ls-files"], cwd=RACINE,
                            capture_output=True, text=True, check=False)
    return [f for f in sortie.stdout.split() if f not in EXCLUS]


@unittest.skipUnless(LISTE.exists(), "pas de liste d'identités privées ici")
class IdentitesPriveesTests(unittest.TestCase):
    def test_aucune_identite_reelle_dans_les_fichiers_versionnes(self):
        cherchees = identites()
        self.assertTrue(cherchees, ".identites-privees est vide")
        trouvees = []
        for chemin in fichiers_versionnes():
            fichier = RACINE / chemin
            try:
                contenu = fichier.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue   # binaire ou illisible : rien à y chercher
            for identite in cherchees:
                if identite in contenu:
                    # On signale l'emplacement, JAMAIS l'identité : le message
                    # d'échec finit dans les journaux de CI.
                    trouvees.append(f"{chemin} (entrée n°{cherchees.index(identite) + 1})")
        self.assertEqual(
            trouvees, [],
            "identités privées dans des fichiers versionnés :\n  "
            + "\n  ".join(sorted(set(trouvees)))
            + "\n(voir .identites-privees pour savoir laquelle)")
