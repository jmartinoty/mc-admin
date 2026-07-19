"""DockerWorkerIntegrity : alignement image/réseau des one-shots pré-créés."""
from __future__ import annotations

import unittest

from adapters.worker_integrity import DockerWorkerIntegrity, WorkerSpec


class _FakeContainer:
    def __init__(self, image: str, networks: dict[str, str]) -> None:
        self.attrs = {
            "Image": image,
            "NetworkSettings": {
                "Networks": {name: {"NetworkID": nid} for name, nid in networks.items()}
            },
        }


class _Containers:
    def __init__(self, by_name: dict) -> None:
        self._by_name = by_name

    def get(self, name: str):
        value = self._by_name.get(name)
        if value is None:
            raise RuntimeError(f"404 Client Error: Not Found (no such container: {name})")
        if isinstance(value, Exception):
            raise value
        return value


class _FakeClient:
    def __init__(self, by_name: dict) -> None:
        self.containers = _Containers(by_name)


SELF = _FakeContainer("sha256:aaa", {"mc_playit": "net-1", "internal": "net-9"})


def _integrity(by_name: dict, specs: list[WorkerSpec]) -> DockerWorkerIntegrity:
    by_name = {"self-id": SELF, **by_name}
    return DockerWorkerIntegrity(specs, self_container_id="self-id", client=_FakeClient(by_name))


class TestStatuses(unittest.TestCase):
    def test_aligned_worker_is_ok(self):
        integrity = _integrity(
            {"mc-restore": _FakeContainer("sha256:aaa", {"mc_playit": "net-1"})},
            [WorkerSpec("mc-restore", "Outil de restauration", same_image=True)],
        )
        check = integrity.statuses()[0]
        self.assertIs(check.ok, True)

    def test_stale_image_is_flagged(self):
        integrity = _integrity(
            {"mc-restore": _FakeContainer("sha256:OLD", {"mc_playit": "net-1"})},
            [WorkerSpec("mc-restore", "Outil de restauration", same_image=True)],
        )
        check = integrity.statuses()[0]
        self.assertIs(check.ok, False)
        self.assertIn("ancienne image", check.detail)
        self.assertIn("--force-recreate mc-restore", check.detail)

    def test_different_image_tolerated_when_not_same_image(self):
        integrity = _integrity(
            {"mc-backup-profile": _FakeContainer("sha256:itzg", {"mc_playit": "net-1"})},
            [WorkerSpec("mc-backup-profile", "Outil de sauvegarde")],
        )
        self.assertIs(integrity.statuses()[0].ok, True)

    def test_stale_network_is_flagged(self):
        integrity = _integrity(
            {"mc-backup-profile": _FakeContainer("sha256:itzg", {"mc_playit": "net-OLD"})},
            [WorkerSpec("mc-backup-profile", "Outil de sauvegarde")],
        )
        check = integrity.statuses()[0]
        self.assertIs(check.ok, False)
        self.assertIn("réseau", check.detail)
        self.assertIn("mc_playit", check.detail)

    def test_network_unknown_to_self_is_tolerated(self):
        # mc-updater vit sur un réseau que mc-admin ne partage pas : rien à
        # comparer, on ne peut pas conclure à un désalignement.
        integrity = _integrity(
            {"mc-updater": _FakeContainer("sha256:cli", {"autre_reseau": "net-x"})},
            [WorkerSpec("mc-updater", "Outil de mise à jour")],
        )
        self.assertIs(integrity.statuses()[0].ok, True)

    def test_missing_container_is_flagged_with_create_hint(self):
        integrity = _integrity({}, [WorkerSpec("mc-restore", "Outil de restauration")])
        check = integrity.statuses()[0]
        self.assertIs(check.ok, False)
        self.assertIn("absent", check.detail)
        self.assertIn("create mc-restore", check.detail)

    def test_proxy_error_is_unknown(self):
        integrity = _integrity(
            {"mc-restore": ConnectionError("proxy down")},
            [WorkerSpec("mc-restore", "Outil de restauration")],
        )
        self.assertIsNone(integrity.statuses()[0].ok)

    def test_unknown_self_is_unknown(self):
        integrity = DockerWorkerIntegrity(
            [WorkerSpec("mc-restore", "Outil de restauration")],
            self_container_id="ghost",
            client=_FakeClient({"mc-restore": _FakeContainer("sha256:aaa", {})}),
        )
        self.assertIsNone(integrity.statuses()[0].ok)


class TestIssues(unittest.TestCase):
    def test_issues_filtered_by_kind(self):
        integrity = _integrity(
            {
                "mc-restore": _FakeContainer("sha256:OLD", {}),
                "mc-backup-profile": _FakeContainer("sha256:x", {"mc_playit": "net-OLD"}),
            },
            [
                WorkerSpec("mc-restore", "Outil de restauration",
                           same_image=True, kinds=frozenset({"restore"})),
                WorkerSpec("mc-backup-profile", "Outil de sauvegarde",
                           kinds=frozenset({"backup"})),
            ],
        )
        restore_issues = integrity.issues("restore")
        self.assertEqual(len(restore_issues), 1)
        self.assertIn("Outil de restauration", restore_issues[0])
        backup_issues = integrity.issues("backup")
        self.assertEqual(len(backup_issues), 1)
        self.assertIn("Outil de sauvegarde", backup_issues[0])

    def test_unknown_blocks_only_when_requested(self):
        integrity = _integrity(
            {"mc-restore": ConnectionError("proxy down")},
            [WorkerSpec("mc-restore", "Outil de restauration",
                        kinds=frozenset({"restore"}))],
        )
        self.assertEqual(integrity.issues("restore"), [])
        strict = integrity.issues("restore", include_unknown=True)
        self.assertEqual(len(strict), 1)
        self.assertIn("vérification impossible", strict[0])

    def test_aligned_worker_produces_no_issue(self):
        integrity = _integrity(
            {"mc-restore": _FakeContainer("sha256:aaa", {"mc_playit": "net-1"})},
            [WorkerSpec("mc-restore", "Outil de restauration",
                        same_image=True, kinds=frozenset({"restore"}))],
        )
        self.assertEqual(integrity.issues("restore", include_unknown=True), [])


if __name__ == "__main__":
    unittest.main()
