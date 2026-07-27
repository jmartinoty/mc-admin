"""Tests de l'adapter DoormanPort (client docker-py factice)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters.doorman import DockerDoorman, NotConfiguredDoorman
from domain.errors import MaintenanceUnavailable, UnauthorizedContainer


class FakeDoormanContainer:
    def __init__(self, name="mc-doorman", status="exited", stop_sticks=False):
        self.name = name
        self.status = status
        self.started = False
        self.stopped = False
        self._stop_sticks = stop_sticks  # simule un portier qui refuse de mourir

    def start(self):
        self.started = True
        self.status = "running"

    def stop(self, timeout=None):
        self.stopped = True
        if not self._stop_sticks:
            self.status = "exited"


class FakeContainers:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, name):
        if name not in self._mapping:
            raise KeyError(name)
        return self._mapping[name]


class FakeClient:
    def __init__(self, mapping):
        self.containers = FakeContainers(mapping)


def _adapter(container, config_path, **kwargs):
    kwargs.setdefault("sleep", lambda _s: None)
    ticks = iter(range(1000))
    kwargs.setdefault("monotonic", lambda: next(ticks))
    return DockerDoorman(
        "mc-doorman",
        str(config_path),
        client=FakeClient({"mc-doorman": container}),
        **kwargs,
    )


class TestDockerDoorman(unittest.TestCase):
    def test_engage_ecrit_la_consigne_avant_le_start(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "doorman.json"
            container = FakeDoormanContainer()
            _adapter(container, config).engage("Maintenance — retour 22:30", "À 22:30 !")
            payload = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(
                payload, {"motd": "Maintenance — retour 22:30", "kick": "À 22:30 !"}
            )
            self.assertTrue(container.started)

    def test_engage_refuse_si_deja_en_place_sans_toucher_la_consigne(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "doorman.json"
            container = FakeDoormanContainer(status="running")
            with self.assertRaises(MaintenanceUnavailable):
                _adapter(container, config).engage("m", "k")
            self.assertFalse(config.exists())
            self.assertFalse(container.started)

    def test_engage_refuse_un_conteneur_au_mauvais_nom(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "doorman.json"
            container = FakeDoormanContainer(name="autre-conteneur")
            with self.assertRaises(UnauthorizedContainer):
                _adapter(container, config).engage("m", "k")
            self.assertFalse(container.started)

    def test_engage_traduit_le_conteneur_absent(self):
        adapter = DockerDoorman(
            "mc-doorman", "/tmp/doorman.json", client=FakeClient({})
        )
        with self.assertRaises(MaintenanceUnavailable):
            adapter.engage("m", "k")

    def test_release_arrete_et_attend_la_liberation(self):
        with tempfile.TemporaryDirectory() as directory:
            container = FakeDoormanContainer(status="running")
            _adapter(container, Path(directory) / "doorman.json").release()
            self.assertTrue(container.stopped)

    def test_release_est_idempotent_sur_portier_deja_arrete(self):
        with tempfile.TemporaryDirectory() as directory:
            container = FakeDoormanContainer(status="exited")
            _adapter(container, Path(directory) / "doorman.json").release()
            self.assertFalse(container.stopped)

    def test_release_leve_si_le_portier_tient_toujours_l_adresse(self):
        with tempfile.TemporaryDirectory() as directory:
            container = FakeDoormanContainer(status="running", stop_sticks=True)
            adapter = _adapter(
                container, Path(directory) / "doorman.json", release_timeout=3
            )
            with self.assertRaises(MaintenanceUnavailable) as caught:
                adapter.release()
            self.assertIn("ne s'arrête pas", str(caught.exception))

    def test_is_running_tolere_le_conteneur_absent(self):
        adapter = DockerDoorman(
            "mc-doorman", "/tmp/doorman.json", client=FakeClient({})
        )
        self.assertFalse(adapter.is_running())

    def test_prime_ecrit_la_consigne_sans_demarrer_le_portier(self):
        # V7b : mc-admin pose la consigne « restauration », c'est le worker
        # mc-restore qui prendra le poste plus tard — donc AUCUN start ici.
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "doorman.json"
            container = FakeDoormanContainer()
            _adapter(container, config).prime("Restauration en cours", "Reviens bientôt")
            payload = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(
                payload, {"motd": "Restauration en cours", "kick": "Reviens bientôt"}
            )
            self.assertFalse(container.started)


class TestNotConfiguredDoorman(unittest.TestCase):
    def test_engage_refuse_explicitement(self):
        with self.assertRaises(MaintenanceUnavailable):
            NotConfiguredDoorman().engage("m", "k")

    def test_release_et_is_running_sont_inertes(self):
        doorman = NotConfiguredDoorman()
        doorman.release()  # ne lève pas : rien à relever
        self.assertFalse(doorman.is_running())

    def test_prime_est_inerte(self):
        NotConfiguredDoorman().prime("m", "k")  # ne lève pas : rien à amorcer


if __name__ == "__main__":
    unittest.main()
