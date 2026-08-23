"""Cible de redirection après un formulaire : interne, et rien d'autre.

Deux exigences opposées. Il faut ramener l'utilisateur sur SA page — sinon
fermer le bandeau d'activité depuis la Carte le renvoyait sur les Sauvegardes
(constaté le 23/08/2026). Et il ne faut jamais le rediriger hors du site : une
redirection ouverte sert d'appât d'hameçonnage, puisque le lien part d'un
domaine de confiance.

La règle valide la FORME du chemin plutôt que d'énumérer les pages : une liste
en dur se périme à chaque page ajoutée, et c'est précisément ce qui s'était
produit — elle ne couvrait que 9 pages sur une vingtaine et citait encore
/sessions, fusionnée la veille dans /compte.
"""
from __future__ import annotations

import unittest

from api.routes.common import _redirect_target

DEFAUT = "/backups"


class CheminsInternesTests(unittest.TestCase):
    def test_les_pages_de_l_application_sont_acceptees(self):
        for chemin in ("/", "/players", "/watch", "/map", "/mods", "/compte",
                       "/performance", "/backups", "/audit", "/game",
                       "/notifications", "/servers", "/users", "/api-tokens"):
            with self.subTest(chemin=chemin):
                self.assertEqual(_redirect_target(chemin, DEFAUT), chemin)

    def test_les_sous_pages_sont_acceptees(self):
        """Une fiche joueur est une page comme une autre."""
        self.assertEqual(_redirect_target("/players/Trappeur", DEFAUT),
                         "/players/Trappeur")


class RedirectionOuverteTests(unittest.TestCase):
    """Chaque cas ci-dessous enverrait l'utilisateur hors du site."""

    def test_url_sans_schema_refusee(self):
        """« //evil.tld » est une URL EXTERNE, pas un chemin : le navigateur
        y reprend le schéma courant. Le piège classique."""
        self.assertEqual(_redirect_target("//evil.tld", DEFAUT), DEFAUT)
        self.assertEqual(_redirect_target("//evil.tld/hameconnage", DEFAUT), DEFAUT)

    def test_url_absolue_refusee(self):
        for valeur in ("http://evil.tld", "https://evil.tld/a", "//evil.tld"):
            with self.subTest(valeur=valeur):
                self.assertEqual(_redirect_target(valeur, DEFAUT), DEFAUT)

    def test_antislash_refuse(self):
        """Certains navigateurs traitent « \\ » comme « / »."""
        self.assertEqual(_redirect_target("/\\evil.tld", DEFAUT), DEFAUT)
        self.assertEqual(_redirect_target("\\\\evil.tld", DEFAUT), DEFAUT)

    def test_schema_dangereux_refuse(self):
        for valeur in ("javascript:alert(1)", "data:text/html,<script>",
                       "vbscript:msgbox"):
            with self.subTest(valeur=valeur):
                self.assertEqual(_redirect_target(valeur, DEFAUT), DEFAUT)

    def test_chemin_relatif_refuse(self):
        self.assertEqual(_redirect_target("evil", DEFAUT), DEFAUT)
        self.assertEqual(_redirect_target("../secret", DEFAUT), DEFAUT)

    def test_valeur_vide_ou_absente(self):
        for valeur in ("", "   ", None):
            with self.subTest(valeur=valeur):
                self.assertEqual(_redirect_target(valeur, DEFAUT), DEFAUT)

    def test_longueur_bornee(self):
        """Pas de chemin démesuré : rien de légitime n'en a besoin."""
        self.assertEqual(_redirect_target("/" + "a" * 500, DEFAUT), DEFAUT)


if __name__ == "__main__":
    unittest.main()
