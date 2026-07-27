"""Rendu lisible des notes de release (dialogue du bandeau MAJ).

Les notes viennent de l'API GitHub en Markdown BRUT : affichées telles
quelles (échappées + `pre-line`), les `###`, `**` et retours à la ligne
« durs » du fichier source rendaient le dialogue illisible (constat
Jeremy, 27/07/2026).

Mini-rendu du SOUS-ENSEMBLE Markdown réellement employé par nos notes —
titres `#`..`####`, listes `- `/`* `, `**gras**`, `` `code` ``,
`[lien](https://…)` — en stdlib + markupsafe (déjà là via Jinja), pas de
dépendance. TOUT le texte est échappé d'abord ; seul le balisage produit
ICI est marqué sûr, et seuls les liens http(s) explicites deviennent des
ancres. Les lignes de continuation d'un bloc sont RECOLLÉES : le Markdown
source est enveloppé à ~72 colonnes et ses coupures ne veulent rien dire
à l'écran. Tout le reste (tableaux, images, HTML…) reste du texte inerte.
"""
from __future__ import annotations

import re

from markupsafe import Markup, escape

_HEADING_RE = re.compile(r"^\s*(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
# Appliqués sur du texte DÉJÀ échappé : les motifs ne peuvent pas ouvrir
# de balise eux-mêmes, ils ne font qu'entourer du texte inerte.
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`\n]+)`")


def _inline(text: str) -> str:
    """Gras/code/liens sur une ligne de texte, échappée AVANT tout balisage."""
    out = str(escape(text))
    out = _LINK_RE.sub(
        r'<a href="\2" target="_blank" rel="noopener">\1</a>', out
    )
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _CODE_RE.sub(r"<code>\1</code>", out)
    return out


def render_release_notes(notes: str) -> Markup:
    """Markdown de release -> HTML sûr (titres, listes, paragraphes)."""
    html: list[str] = []
    in_list = False
    block: list[str] = []
    block_kind = ""  # "p" ou "li"

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    def flush() -> None:
        nonlocal block, block_kind, in_list
        if not block:
            return
        text = _inline(" ".join(block))
        if block_kind == "li":
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{text}</li>")
        else:
            close_list()
            html.append(f"<p>{text}</p>")
        block, block_kind = [], ""

    for raw in (notes or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        heading = _HEADING_RE.match(raw)
        if heading:
            flush()
            close_list()
            html.append(f"<h3>{_inline(heading.group(2).strip())}</h3>")
            continue
        bullet = _BULLET_RE.match(raw)
        if bullet:
            flush()
            block, block_kind = [bullet.group(1).strip()], "li"
            continue
        # Ligne de continuation : recollée au bloc courant (paragraphe par défaut).
        if not block_kind:
            block_kind = "p"
        block.append(line)
    flush()
    close_list()
    return Markup("".join(html))
