"""Rendu des notes de release (Markdown GitHub -> HTML sûr, lisible)."""
from __future__ import annotations

import unittest

from api.release_notes import render_release_notes


class TestRenderReleaseNotes(unittest.TestCase):
    def test_titres_puces_et_gras(self):
        html = str(render_release_notes(
            "### Sécurité (nouveau)\n- **2FA** en self-service.\n- Appareils connectés.\n"
        ))
        self.assertIn("<h3>Sécurité (nouveau)</h3>", html)
        self.assertIn("<ul><li><strong>2FA</strong> en self-service.</li>", html)
        self.assertIn("<li>Appareils connectés.</li></ul>", html)

    def test_lignes_enveloppees_recollees(self):
        # Le Markdown source est enveloppé à ~72 colonnes : les coupures de
        # ligne au milieu d'une phrase ne doivent PAS apparaître à l'écran.
        html = str(render_release_notes(
            "- **Double authentification (2FA)** en self-service : activation sur la\n"
            "  nouvelle page « Sécurité » avec n'importe quelle application TOTP.\n"
        ))
        self.assertIn(
            "activation sur la nouvelle page « Sécurité »", html.replace("</strong>", "")
        )
        self.assertEqual(html.count("<li>"), 1)

    def test_paragraphe_par_defaut_et_code(self):
        html = str(render_release_notes("Voir `docs/codes.md` pour le détail.\n"))
        self.assertIn("<p>Voir <code>docs/codes.md</code> pour le détail.</p>", html)

    def test_lien_https_seulement(self):
        html = str(render_release_notes(
            "- [guide](https://example.com/doc) et [piège](javascript:alert(1))\n"
        ))
        self.assertIn('<a href="https://example.com/doc" target="_blank" rel="noopener">guide</a>', html)
        self.assertNotIn('href="javascript', html)

    def test_html_hostile_echappe(self):
        # Les notes viennent d'une API externe : tout HTML reste du texte inerte.
        html = str(render_release_notes('### <script>alert(1)</script>\n- <img src=x onerror=y>\n'))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_vide(self):
        self.assertEqual(str(render_release_notes("")), "")
        self.assertEqual(str(render_release_notes(None)), "")


if __name__ == "__main__":
    unittest.main()
