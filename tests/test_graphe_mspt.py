"""Le graphe MSPT place ses points sur un axe TEMPOREL.

Régression du 23/08/2026 : les points étaient placés à leur RANG
(`step = span / (len(history) - 1)`) alors que les graduations et les repères
étaient placés à leur INSTANT. Tant que la série est complète les deux
coïncident, et le défaut reste invisible — c'est pourquoi il a survécu.

Dès qu'une mesure manque, ils divergent : le trou se contracte à zéro pixel
et TOUT son passé glisse vers la droite. Mesuré sur les données réelles du
NAS : un trou contigu de 12,0 h le 31/07/2026 (710 points au lieu de 721 sur
30 jours), donc 12 h de décalage entre la courbe et ses propres repères de
sauvegarde. Le graphe mentait sans rien signaler.
"""
from __future__ import annotations

import time
import unittest

from api.routes.dashboard import _mspt_chart
from tests.test_services import ServiceTestBase

_PAD = 6.0
_SPAN = 600.0 - 2 * _PAD          # largeur utile du viewBox
_PAS = 900                        # 15 min, le pas de la plage 24 h


def _x(points: str, rang: int) -> float:
    """Abscisse du point `rang` d'une chaîne « x,y x,y … »."""
    return float(points.split()[rang].split(",")[0])


def _serie(depuis_h: float, jusqu_a_h: float, valeur: float = 5.0, pas: int = _PAS):
    """Couples (instant, valeur) de `depuis_h` à `jusqu_a_h` heures avant maintenant."""
    maintenant = time.time()
    debut, fin = maintenant - depuis_h * 3600, maintenant - jusqu_a_h * 3600
    instants = []
    at = debut
    while at <= fin:
        instants.append((at, valeur))
        at += pas
    return instants


class TestAxeTemporel(unittest.TestCase):
    def test_un_point_est_place_a_son_instant_pas_a_son_rang(self):
        """Trois points très rapprochés en DÉBUT de fenêtre restent à gauche.
        Placés au rang, ils s'étaleraient sur toute la largeur."""
        maintenant = time.time()
        histoire = [(maintenant - 24 * 3600 + n * _PAS, 5.0) for n in range(3)]
        chart = _mspt_chart(histoire, hours=24)
        self.assertIsNotNone(chart)
        points = chart["segments"][0]
        # 3 points sur 30 min au sein d'une fenêtre de 24 h : moins de 2 % de
        # la largeur. Au rang, le dernier serait à l'extrême droite (594).
        self.assertLess(_x(points, 2), _PAD + 0.03 * _SPAN)

    def test_un_point_de_maintenant_touche_le_bord_droit(self):
        histoire = [(time.time() - 3600, 5.0), (time.time(), 6.0)]
        chart = _mspt_chart(histoire, hours=24)
        self.assertAlmostEqual(_x(chart["segments"][0], 1), _PAD + _SPAN, delta=1.0)

    def test_le_milieu_de_fenetre_tombe_au_milieu(self):
        maintenant = time.time()
        histoire = [(maintenant - 24 * 3600, 5.0), (maintenant - 12 * 3600, 6.0)]
        chart = _mspt_chart(histoire, hours=24)
        self.assertAlmostEqual(_x(chart["segments"][0], 1), _PAD + 0.5 * _SPAN, delta=1.5)


class TestTrouDeMesure(unittest.TestCase):
    """Le cas réel du 31/07 : 12 h sans aucune mesure."""

    def _histoire_trouee(self):
        # 6 h de mesures, 12 h de silence, 6 h de mesures.
        return _serie(24, 18) + _serie(6, 0)

    def test_le_trou_coupe_la_courbe_en_deux(self):
        chart = _mspt_chart(self._histoire_trouee(), hours=24)
        self.assertEqual(len(chart["segments"]), 2)
        self.assertEqual(chart["trous"], 1)

    def test_le_passe_du_trou_ne_glisse_pas(self):
        """LE test de la régression. Le dernier point avant le trou date de
        18 h avant maintenant, sur une fenêtre de 24 h : il appartient au
        premier quart de la largeur. Placé au rang, il serait au milieu."""
        chart = _mspt_chart(self._histoire_trouee(), hours=24)
        avant_trou = chart["segments"][0]
        dernier = _x(avant_trou, len(avant_trou.split()) - 1)
        attendu = _PAD + (6.0 / 24.0) * _SPAN          # 24 h - 18 h = 6 h écoulées
        self.assertAlmostEqual(dernier, attendu, delta=2.0)
        # Et surtout : loin de là où le placement au rang l'aurait mis.
        self.assertLess(dernier, _PAD + 0.40 * _SPAN)

    def test_une_serie_continue_reste_d_un_seul_tenant(self):
        chart = _mspt_chart(_serie(24, 0), hours=24)
        self.assertEqual(len(chart["segments"]), 1)
        self.assertEqual(chart["trous"], 0)

    def test_plusieurs_trous_sont_comptes(self):
        histoire = _serie(24, 20) + _serie(16, 12) + _serie(8, 0)
        chart = _mspt_chart(histoire, hours=24)
        self.assertEqual(len(chart["segments"]), 3)
        self.assertEqual(chart["trous"], 2)


class TestBornes(unittest.TestCase):
    def test_serie_trop_courte_ne_trace_rien(self):
        self.assertIsNone(_mspt_chart([(time.time(), 5.0)], hours=24))
        self.assertIsNone(_mspt_chart([], hours=24))

    def test_le_pic_et_le_depassement_suivent_les_valeurs(self):
        maintenant = time.time()
        histoire = [(maintenant - 3600, 2.0), (maintenant - 1800, 61.5), (maintenant, 3.0)]
        chart = _mspt_chart(histoire, hours=24, threshold_ms=50.0)
        self.assertEqual(chart["max"], 61.5)
        self.assertTrue(chart["over"])

    def test_un_point_plus_vieux_que_la_fenetre_est_ramene_au_bord(self):
        """Une série plus large que la plage demandée ne doit pas déborder
        du cadre (abscisse négative = trait hors du SVG)."""
        maintenant = time.time()
        histoire = [(maintenant - 48 * 3600, 5.0), (maintenant - 36 * 3600, 6.0),
                    (maintenant - 24 * 3600, 7.0)]
        chart = _mspt_chart(histoire, hours=24)
        for rang in range(3):
            self.assertGreaterEqual(_x(chart["segments"][0], rang), _PAD)



class TestBornesDeNavigation(ServiceTestBase):
    """Le service RAMÈNE toute borne aberrante dans le possible.

    Le navigateur n'envoie que des bornes, et il peut envoyer n'importe
    quoi : URL bricolée, horloge cliente décalée, composant qui dérape.
    Comme `performance_snapshot` retombe sur 24 h plutôt que de lever, on
    corrige silencieusement — mais on retourne la fenêtre RÉELLEMENT
    servie, pour que la vue n'affiche jamais une plage qu'elle n'a pas.
    """

    ANCRE = 1_800_000_000.0          # « maintenant » figé pour tous ces tests

    def _fenetre(self, debut, fin):
        self.service.mspt_range(self.owner, debut, fin, now=self.ANCRE)
        return self.metrics.last_range

    def test_le_futur_est_ramene_a_maintenant(self):
        _, fin = self._fenetre(self.ANCRE - 3600, self.ANCRE + 10 * 86400)
        self.assertAlmostEqual(fin, self.ANCRE, delta=1)

    def test_avant_la_retention_est_ramene_au_plancher(self):
        """Prometheus ne garde que 30 jours : demander l'an dernier ne doit
        pas produire un graphe vide sans explication."""
        debut, _ = self._fenetre(self.ANCRE - 365 * 86400, self.ANCRE - 300 * 86400)
        self.assertAlmostEqual(debut, self.ANCRE - 30 * 86400, delta=1)

    def test_une_fenetre_trop_etroite_est_elargie(self):
        debut, fin = self._fenetre(self.ANCRE - 10, self.ANCRE)
        self.assertAlmostEqual(fin - debut, self.service.PERFORMANCE_SPAN_MIN, delta=1)

    def test_une_fenetre_trop_large_est_reduite(self):
        debut, fin = self._fenetre(self.ANCRE - 900 * 86400, self.ANCRE)
        self.assertAlmostEqual(fin - debut, self.service.PERFORMANCE_SPAN_MAX, delta=1)

    def test_bornes_inversees_ne_plantent_pas(self):
        debut, fin = self._fenetre(self.ANCRE, self.ANCRE - 86400)
        self.assertLess(debut, fin)

    def test_bornes_illisibles_retombent_sur_24h(self):
        for cochonnerie in ("", None, "demain", float("nan"), float("inf")):
            debut, fin = self._fenetre(cochonnerie, cochonnerie)
            self.assertAlmostEqual(fin - debut, 24 * 3600, delta=1,
                                   msg=f"entrée : {cochonnerie!r}")

    def test_une_fenetre_valide_passe_intacte(self):
        voulu = (self.ANCRE - 3 * 86400, self.ANCRE - 2 * 86400)
        debut, fin = self._fenetre(*voulu)
        self.assertAlmostEqual(debut, voulu[0], delta=1)
        self.assertAlmostEqual(fin, voulu[1], delta=1)

    def test_un_role_en_lecture_seule_peut_naviguer(self):
        """Même barrière que le reste de la page (STATUS) : c'est de la
        lecture, quiconque voit le graphe peut s'y déplacer."""
        self.service.mspt_range(self.friend, self.ANCRE - 86400, self.ANCRE,
                                now=self.ANCRE)

    def test_sans_permission_status_la_navigation_est_refusee(self):
        from domain.errors import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.service.mspt_range(self.guest, self.ANCRE - 86400, self.ANCRE,
                                    now=self.ANCRE)


class TestCourbeJoueurs(unittest.TestCase):
    """P4 : la courbe joueurs partage l'axe temporel du MSPT mais porte sa
    propre échelle (0 -> pic de la fenêtre, plancher 2)."""

    def _chart(self, joueurs):
        return _mspt_chart(_serie(24, 0), hours=24, players=joueurs)

    def test_sans_joueurs_pas_de_courbe(self):
        chart = self._chart(())
        self.assertEqual(chart["joueurs"], [])
        self.assertEqual(chart["joueurs_max"], 0)

    def test_l_echelle_suit_le_pic_de_la_fenetre(self):
        maintenant = time.time()
        joueurs = [(maintenant - 2 * 3600, 0.0), (maintenant - 3600, 4.0), (maintenant, 1.0)]
        chart = self._chart(joueurs)
        self.assertEqual(chart["joueurs_max"], 4)
        ys = [float(pt.split(",")[1]) for pt in chart["joueurs"][0].split()]
        # 0 joueur au ras du bas, 4 joueurs au sommet (échelle pleine hauteur)
        self.assertAlmostEqual(ys[0], 140.0 - 6.0, delta=0.5)
        self.assertAlmostEqual(ys[1], 6.0, delta=0.5)

    def test_un_joueur_seul_ne_remplit_pas_la_hauteur(self):
        """Plancher d'échelle à 2 : une soirée solo ne doit pas dessiner un
        mur — la courbe joueurs est du contexte, pas le sujet."""
        maintenant = time.time()
        joueurs = [(maintenant - 3600, 1.0), (maintenant, 1.0)]
        chart = self._chart(joueurs)
        self.assertEqual(chart["joueurs_max"], 2)
        y = float(chart["joueurs"][0].split()[0].split(",")[1])
        self.assertAlmostEqual(y, 6.0 + (140.0 - 12.0) / 2, delta=1.0)

    def test_un_trou_coupe_aussi_la_courbe_joueurs(self):
        maintenant = time.time()
        joueurs = ([(maintenant - 24 * 3600 + n * _PAS, 2.0) for n in range(8)]
                   + [(maintenant - 3600 + n * _PAS, 3.0) for n in range(4)])
        chart = self._chart(joueurs)
        self.assertEqual(len(chart["joueurs"]), 2)

if __name__ == "__main__":
    unittest.main()
