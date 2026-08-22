"""Le ton de l'interface : impersonnel, jamais le tutoiement.

mc-admin s'est longtemps adressé à son unique utilisateur en le tutoyant.
Devenu un produit publié, il emploie la 3e personne (choix de Jeremy,
22/08/2026). Ce test empêche le tutoiement de revenir par une modification
future — même esprit que le test qui interdit les `confirm()` natifs.

Il inspecte les TEXTES d'interface : gabarits et messages des routes. Les
commentaires de code et les docstrings, eux, tutoient qui ils veulent.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent / "app" / "api"

# Mots qui trahissent un tutoiement. « ta » et « tes » sont volontairement
# absents : trop d'occurrences légitimes en anglais (data, notes) et dans les
# attributs HTML.
TUTOIEMENT = re.compile(
    r"\b(tu|ton|toi)\b"
    r"|\b(?:réessaie|choisis|saisis|scanne|installe|ajoute|récupère|envoie|protège)\s"
    r"|\bveux-tu\b|\bt'y\b|\bt'écrire\b",
    re.IGNORECASE,
)

# Faux positifs connus : du vocabulaire du jeu ou du code, pas une adresse au
# lecteur.
TOLERE = re.compile(
    r"button|<td|metric-label|content=|data-|\.tu\b|autocomplete"
    r"|# |// |\"\"\"|'''",
)


def textes(chemin: Path) -> list[tuple[int, str]]:
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    return [(n, ligne) for n, ligne in enumerate(lignes, 1)
            if TUTOIEMENT.search(ligne) and not TOLERE.search(ligne)]


class TonTests(unittest.TestCase):
    def test_les_gabarits_ne_tutoient_pas(self):
        fautifs = []
        for chemin in sorted(RACINE.glob("templates/**/*.html")):
            for numero, ligne in textes(chemin):
                fautifs.append(f"{chemin.name}:{numero} — {ligne.strip()[:80]}")
        self.assertEqual(fautifs, [], "tutoiement dans l'interface :\n" + "\n".join(fautifs))

    def test_les_messages_des_routes_ne_tutoient_pas(self):
        fautifs = []
        for chemin in sorted(RACINE.glob("routes/*.py")):
            for numero, ligne in textes(chemin):
                if '"' not in ligne and "'" not in ligne:
                    continue  # pas un message affiché
                fautifs.append(f"{chemin.name}:{numero} — {ligne.strip()[:80]}")
        self.assertEqual(fautifs, [], "tutoiement dans les messages :\n" + "\n".join(fautifs))


if __name__ == "__main__":
    unittest.main()
