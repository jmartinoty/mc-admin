"""QR code du lien de double authentification.

Adaptateur mince au-dessus de `segno` : le rendu SVG est produit ici pour
rester maître du balisage inséré dans les pages (dimensions, couleurs
héritées du thème, attributs d'accessibilité).

POURQUOI UNE DÉPENDANCE, alors que le RCON, le TOTP et le portier sont
écrits en stdlib (CLAUDE.md) — la doctrine dit « pas de dépendance quand la
stdlib suffit », et c'est le « suffit » qui fait défaut ici. Un encodeur QR
demande le corps fini GF(256), Reed-Solomon, huit masques et leurs pénalités,
plus les tables de capacité de la norme : environ 300 lignes, dont un premier
jet a produit des matrices s'écartant de la référence sans cause identifiée.
Un QR qui ressemble à un QR mais ne se scanne pas est pire que pas de QR, et
c'est la seule porte d'entrée de la 2FA. segno est en Python pur et fait
300 Ko.
"""
from __future__ import annotations

import io
import re

import segno


def matrice(donnees: str) -> list[list[int]] | None:
    """Matrice du QR (1 = module sombre), ou None si les données sortent de la
    norme. Exposée pour que les tests relisent ce que produit RÉELLEMENT cette
    fonction, plutôt que de faire confiance à la bibliothèque sur parole."""
    code = _encoder(donnees)
    if code is None:
        return None
    return [[1 if module else 0 for module in ligne] for ligne in code.matrix]


def _encoder(donnees: str):
    """Niveau de correction M, FIXÉ : segno l'augmente sinon dès que la place
    le permet, ce qui rendrait le résultat dépendant de la longueur des
    données. M corrige 15 % du code — largement de quoi absorber un reflet
    d'écran, et le comportement reste prévisible."""
    try:
        return segno.make(donnees, micro=False, error="m", boost_error=False)
    except Exception:      # noqa: BLE001 - données hors de portée de la norme
        return None


def svg(donnees: str, module: int = 4, marge: int = 4) -> str | None:
    """QR code en SVG, prêt à insérer dans une page. None si les données ne
    peuvent pas être encodées — l'appelant retombe alors sur la clé à
    recopier, jamais sur une image approximative.

    Le fond est explicitement blanc et les modules noirs : un QR doit garder
    son contraste quel que soit le thème de la page, un lecteur ne sachant pas
    lire un code clair sur fond sombre.
    """
    code = _encoder(donnees)
    if code is None:
        return None
    tampon = io.BytesIO()
    code.save(tampon, kind="svg", scale=module, border=marge,
              dark="#000000", light="#ffffff", xmldecl=False, svgns=True)
    image = tampon.getvalue().decode("utf-8")

    # segno donne au SVG des dimensions en pixels, SANS viewBox. Une feuille de
    # style qui impose une autre taille le RECADRE alors au lieu de le
    # redimensionner : le code se retrouve amputé, et un QR amputé ne se scanne
    # pas (constaté le 23/08/2026). Le viewBox rend l'image redimensionnable.
    dimensions = re.search(r'width="(\d+)"\s+height="(\d+)"', image)
    if dimensions is not None:
        largeur, hauteur = dimensions.group(1), dimensions.group(2)
        image = image.replace(
            dimensions.group(0),
            f'{dimensions.group(0)} viewBox="0 0 {largeur} {hauteur}"', 1)

    # Accessibilité : le lecteur d'écran doit savoir ce qu'est cette image.
    return image.replace(
        "<svg ",
        '<svg role="img" aria-label="QR code de configuration de la double '
        'authentification" ',
        1,
    )
