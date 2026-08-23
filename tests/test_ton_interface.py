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

# Tout le code applicatif, pas seulement les routes et les gabarits : des
# messages d'interface vivent aussi dans le domaine (services) et dans le
# JavaScript. Quatre tutoiements y dormaient, hors de portée du test.
RACINE = Path(__file__).resolve().parent.parent / "app"

# Ce qui trahit un tutoiement, par MOTIF plutôt que par vocabulaire.
#
# Une simple liste de verbes ne marche pas : en français, l'impératif deuxième
# personne est homographe de l'indicatif troisième personne et parfois d'un nom
# commun. « Le serveur redémarre », « une ferme à mobs », « active » dans un
# attribut : autant de faux positifs qui noieraient les vrais.
#
# On cherche donc trois motifs sans ambiguïté, plus les pronoms :
#   1. l'impératif NÉGATIF          « ne ferme pas », « n'oublie pas »
#   2. l'impératif PRONOMINALISÉ    « ajoute-le », « glisse-y », « colle-la »
#   3. un verbe d'action en DÉBUT de phrase, liste restreinte aux verbes qui
#      ne sont pas des noms communs
#
# Les trois cas réels qui avaient échappé au premier jet sont couverts :
# « ne ferme pas l'onglet » (1), « ajoute-le au groupe » (2), « Recharge la
# page » (3), plus « je te le rappellerai » par le pronom « te ».
_VERBES_DEBUT = (
    "réessaie", "choisis", "saisis", "scanne", "installe", "récupère",
    "recharge", "renseigne", "glisse", "colle", "télécharge", "clique",
    "coche", "remplis", "confirme", "sélectionne", "vérifie", "crée", "ouvre",
)
_PRONOMS_ENCLITIQUES = ("le", "la", "les", "moi", "toi", "y", "en", "lui")

TUTOIEMENT = re.compile(
    # Pronoms de la deuxième personne du singulier.
    r"\b(tu|ton|toi|te)\b"
    r"|\bveux-tu\b|\bt'y\b|\bt'écrire\b"
    # 1. Impératif négatif, sur une liste restreinte : « le jeu ne répond
    #    pas » est de la troisième personne, morphologiquement identique —
    #    seule une liste de verbes d'interface évite ce faux positif.
    r"|\bn[e']\s*(?:ferme|oublie|quitte|coupe|touche|clique|recharge|"
    r"hésite|t'inquiète)\w*\s+pas\b"
    # 2. Impératif pronominalisé : verbe suivi d'un trait d'union et d'un pronom.
    # Le « (?!-) » écarte les identifiants composés : « sauvegarde-en-cours »
    # n'est pas un impératif suivi du pronom « en ».
    r"|\b\w{3,}[éez]-(?:" + "|".join(_PRONOMS_ENCLITIQUES) + r")\b(?!-)"
    # 3. Verbe d'action en début de phrase (après un début de ligne, une
    #    ponctuation forte, une balise fermante ou un numéro de liste).
    r"|(?:^|[.!?:]\s+|>\s*|\d\.\s+)(?:" + "|".join(_VERBES_DEBUT) + r")\b",
    re.IGNORECASE | re.MULTILINE,
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
        for chemin in sorted(RACINE.glob("api/templates/**/*.html")):
            for numero, ligne in textes(chemin):
                fautifs.append(f"{chemin.name}:{numero} — {ligne.strip()[:80]}")
        self.assertEqual(fautifs, [], "tutoiement dans l'interface :\n" + "\n".join(fautifs))

    def test_les_messages_du_code_ne_tutoient_pas(self):
        """Routes, services du domaine et JavaScript : un message d'interface
        peut naître n'importe où."""
        fautifs = []
        for motif in ("api/**/*.py", "domain/**/*.py", "adapters/**/*.py",
                      "api/static/*.js"):
            for chemin in sorted(RACINE.glob(motif)):
                for numero, ligne in textes(chemin):
                    if '"' not in ligne and "'" not in ligne:
                        continue  # pas un message affiché
                    relatif = chemin.relative_to(RACINE)
                    fautifs.append(f"{relatif}:{numero} — {ligne.strip()[:80]}")
        self.assertEqual(fautifs, [], "tutoiement dans les messages :\n" + "\n".join(fautifs))


if __name__ == "__main__":
    unittest.main()


class DetectionTests(unittest.TestCase):
    """Le détecteur lui-même : il doit attraper les vrais cas SANS crier au
    loup. Un test qui signale des faux positifs finit désactivé, et ne protège
    alors plus rien.

    Les cas ci-dessous sont réels : à gauche des tutoiements qui ont
    effectivement échappé au premier jet, à droite les tournures homographes
    qui l'avaient fait crier à tort.
    """

    TUTOIEMENTS = (
        "ne ferme pas cet onglet",              # impératif négatif
        "ajoute-le au groupe puis re-clique",   # impératif pronominalisé
        "rouvre-le avant de lancer",
        "Copie-le maintenant",
        "je te le rappellerai demain",          # pronom « te »
        "Recharge la page dans un instant.",    # verbe en début de phrase
        "scanne le QR avec ton téléphone",
    )

    FORMULATIONS_CORRECTES = (
        "Le serveur redémarre.",                 # 3e personne, pas impératif
        "le jeu ne répond pas",                  # négation à la 3e personne
        "une ferme à mobs qui déborde",          # nom commun
        "sauvegarde-en-cours",                   # identifiant composé
        "active == 'status'",                    # attribut de gabarit
        "Le serveur vérifie la vitesse en élytres",
        "Recharger la page manuellement",        # la bonne tournure
        "cet onglet doit rester ouvert",
    )

    def test_attrape_les_tutoiements(self):
        for texte in self.TUTOIEMENTS:
            with self.subTest(texte=texte):
                self.assertIsNotNone(TUTOIEMENT.search(texte),
                                     f"tutoiement non détecté : {texte!r}")

    def test_ne_signale_pas_les_formulations_correctes(self):
        for texte in self.FORMULATIONS_CORRECTES:
            with self.subTest(texte=texte):
                self.assertIsNone(TUTOIEMENT.search(texte),
                                  f"faux positif : {texte!r}")
