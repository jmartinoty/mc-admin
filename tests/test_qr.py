"""QR code de la double authentification.

L'encodage lui-même est confié à segno (cf. app/api/qr.py). Ces tests ne le
retestent pas : ils vérifient ce dont NOUS sommes responsables — que le code
produit contient bien l'URI qu'on lui a donnée, que le SVG est utilisable
dans une page, et qu'un cas hors norme ne produit pas d'image bancale.

La relecture se fait avec un décodeur écrit ici, indépendant de segno : c'est
lui qui garantit qu'un téléphone retrouvera l'URI, et non la seule confiance
en la bibliothèque.
"""
from __future__ import annotations

import unittest

from api import qr

URI = ("otpauth://totp/mc-admin:Trappeur?secret=3KXAB3U3462DIMEDGBIXG4L73GINRVJQ"
       "&issuer=mc-admin&algorithm=SHA1&digits=6&period=30")

MASQUES = (
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0,
)
# (version, niveau) -> (correction par bloc, [(nombre de blocs, données par
# bloc), …]). À partir de la version 8, les blocs sont de tailles INÉGALES :
# la norme répartit le reste sur les derniers.
_BLOCS = {
    (1, "M"): (10, [(1, 16)]),  (2, "M"): (16, [(1, 28)]),
    (3, "M"): (26, [(1, 44)]),  (4, "M"): (18, [(2, 32)]),
    (5, "M"): (24, [(2, 43)]),  (6, "M"): (16, [(4, 27)]),
    (7, "M"): (18, [(4, 31)]),  (8, "M"): (22, [(2, 38), (2, 39)]),
    (9, "M"): (22, [(3, 36), (2, 37)]),
}
_ALIGNEMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
               6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46]}


def relire(m: list[list[int]]) -> str:
    """Décode une matrice : format, dé-masquage, parcours, désentrelacement."""
    taille = len(m)
    version = (taille - 17) // 4
    positions = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                 (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    bits = 0
    for i, j in positions:
        bits = (bits << 1) | m[i][j]
    valeur = (bits ^ 0b101010000010010) >> 10
    niveau = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}[valeur >> 3]
    masque = valeur & 0b111

    fixe = [[False] * taille for _ in range(taille)]
    for haut, gauche in ((0, 0), (0, taille - 7), (taille - 7, 0)):
        for di in range(-1, 8):
            for dj in range(-1, 8):
                i, j = haut + di, gauche + dj
                if 0 <= i < taille and 0 <= j < taille:
                    fixe[i][j] = True
    for k in range(taille):
        fixe[6][k] = fixe[k][6] = True
    for k in range(9):
        fixe[8][k] = fixe[k][8] = True
    for k in range(8):
        fixe[8][taille - 1 - k] = fixe[taille - 1 - k][8] = True
    if version >= 7:
        # Information de version : deux blocs de 6×3, à réserver eux aussi.
        for i in range(6):
            for j in range(3):
                fixe[i][taille - 11 + j] = True
                fixe[taille - 11 + j][i] = True
    for ci in _ALIGNEMENT[version]:
        for cj in _ALIGNEMENT[version]:
            coin = (ci <= 8 and cj <= 8) or (ci <= 8 and cj >= taille - 9) \
                or (ci >= taille - 9 and cj <= 8)
            if not coin:
                for di in range(-2, 3):
                    for dj in range(-2, 3):
                        fixe[ci + di][cj + dj] = True

    lus: list[int] = []
    colonne, montant = taille - 1, True
    while colonne > 0:
        if colonne == 6:
            colonne -= 1
        for ligne in (range(taille - 1, -1, -1) if montant else range(taille)):
            for j in (colonne, colonne - 1):
                if not fixe[ligne][j]:
                    bit = m[ligne][j]
                    if MASQUES[masque](ligne, j):
                        bit ^= 1
                    lus.append(bit)
        colonne -= 2
        montant = not montant

    mots = [int("".join(str(b) for b in lus[i:i + 8]), 2)
            for i in range(0, len(lus) - 7, 8)]
    _ec, groupes = _BLOCS[(version, niveau)]
    tailles = [taille for nombre, taille in groupes for _ in range(nombre)]
    blocs: list[list[int]] = [[] for _ in tailles]
    curseur = 0
    # Entrelacement : on prend un codeword par bloc, en sautant les blocs
    # courts une fois épuisés.
    for rang in range(max(tailles)):
        for indice, taille in enumerate(tailles):
            if rang < taille:
                blocs[indice].append(mots[curseur])
                curseur += 1
    flux = "".join(f"{mot:08b}" for bloc in blocs for mot in bloc)
    assert flux[:4] == "0100", "mode octet attendu"
    longueur = int(flux[4:12], 2)
    return bytes(int(flux[12 + i * 8:20 + i * 8], 2)
                 for i in range(longueur)).decode("utf-8")


class ContenuTests(unittest.TestCase):
    """Ce qui compte vraiment : un téléphone doit y retrouver l'URI."""

    def test_le_qr_contient_l_uri_de_double_authentification(self):
        self.assertEqual(relire(qr.matrice(URI)), URI)

    def test_diverses_longueurs(self):
        for longueur in (1, 25, 60, 100, 130):
            with self.subTest(longueur=longueur):
                donnees = "".join("abcdefghij"[i % 10] for i in range(longueur))
                self.assertEqual(relire(qr.matrice(donnees)), donnees)


class RenduTests(unittest.TestCase):
    def setUp(self):
        self.image = qr.svg(URI)

    def test_svg_bien_forme(self):
        self.assertTrue(self.image.lstrip().startswith("<svg "))
        self.assertIn("</svg>", self.image)

    def test_accessible(self):
        self.assertIn('role="img"', self.image)
        self.assertIn("aria-label=", self.image)

    def test_aucun_script_injecte(self):
        self.assertNotIn("<script", self.image)
        self.assertNotIn("javascript:", self.image)

    def test_contraste_explicite(self):
        """Un lecteur ne sait pas lire un code clair sur fond sombre : le QR
        garde ses couleurs quel que soit le thème de la page."""
        image = self.image.lower()
        self.assertTrue("#ffffff" in image or "#fff" in image)
        self.assertTrue("#000000" in image or "#000" in image)

    def test_redimensionnable_sans_recadrage(self):
        """Non-régression : segno produit un SVG dimensionné en pixels SANS
        viewBox. Imposer une autre taille en CSS le recadrait alors au lieu de
        le redimensionner — le code se retrouvait amputé, donc illisible par un
        lecteur (constaté sur le staging le 23/08/2026)."""
        import re as _re
        balise = _re.search(r"<svg[^>]*>", self.image).group(0)
        self.assertIn("viewBox=", balise)
        largeur = _re.search(r'width="(\d+)"', balise).group(1)
        hauteur = _re.search(r'height="(\d+)"', balise).group(1)
        self.assertIn(f'viewBox="0 0 {largeur} {hauteur}"', balise)

    def test_taille_raisonnable_pour_une_page(self):
        self.assertLess(len(self.image), 20_000)


class LimitesTests(unittest.TestCase):
    def test_donnees_trop_longues_rendent_None(self):
        """Hors de portée de la norme : pas d'image bancale, l'appelant
        retombe sur la clé à recopier."""
        self.assertIsNone(qr.svg("x" * 5000))


if __name__ == "__main__":
    unittest.main()
