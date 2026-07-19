"""Tests du démarrage et de l'arrêt des tâches de fond FastAPI."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from adapters.credentials_store import JsonCredentials
from adapters.display_names import JsonDisplayNames
from api.app import _running_background_tasks, create_app
from config import Settings


class FakeHasher:
    def hash(self, password: str) -> str:
        return f"hash:{password}"

    def verify(self, stored_hash: str, password: str) -> bool:
        return stored_hash == f"hash:{password}"


class RecordingTask:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"start failed: {self.name}")

    def stop(self) -> None:
        self.events.append(f"stop:{self.name}")
        if self.fail_stop:
            raise RuntimeError(f"stop failed: {self.name}")


class TestBackgroundTaskLifecycle(unittest.TestCase):
    def test_tasks_stop_in_reverse_start_order(self):
        events = []
        first = RecordingTask("first", events)
        second = RecordingTask("second", events)
        third = RecordingTask("third", events)

        with _running_background_tasks((
            ("first", first),
            ("second", second),
            ("third", third),
        )):
            events.append("app:running")

        self.assertEqual(events, [
            "start:first",
            "start:second",
            "start:third",
            "app:running",
            "stop:third",
            "stop:second",
            "stop:first",
        ])

    def test_optional_tasks_are_independent(self):
        events = []
        perf = RecordingTask("perf", events)
        health = RecordingTask("health", events)

        with _running_background_tasks((
            ("health", None),
            ("perf", perf),
            ("mods", None),
        )):
            pass
        with _running_background_tasks((
            ("health", health),
            ("perf", None),
        )):
            pass

        self.assertEqual(events, [
            "start:perf",
            "stop:perf",
            "start:health",
            "stop:health",
        ])

    def test_start_failure_rolls_back_started_and_failing_tasks(self):
        events = []
        first = RecordingTask("first", events)
        failing = RecordingTask("failing", events, fail_start=True)
        never_started = RecordingTask("never", events)

        with self.assertRaisesRegex(RuntimeError, "start failed: failing"):
            with _running_background_tasks((
                ("first", first),
                ("failing", failing),
                ("never", never_started),
            )):
                self.fail("l'application ne doit pas démarrer")

        self.assertEqual(events, [
            "start:first",
            "start:failing",
            "stop:failing",
            "stop:first",
        ])

    def test_stop_failure_does_not_skip_remaining_cleanup(self):
        events = []
        first = RecordingTask("first", events)
        failing = RecordingTask("failing", events, fail_stop=True)
        last = RecordingTask("last", events)

        with _running_background_tasks((
            ("first", first),
            ("failing", failing),
            ("last", last),
        )):
            pass

        self.assertEqual(events, [
            "start:first",
            "start:failing",
            "start:last",
            "stop:last",
            "stop:failing",
            "stop:first",
        ])

    def test_create_app_wires_every_task_into_the_lifespan(self):
        events = []
        names = (
            "backup",
            "player",
            "tempban",
            "restart",
            "restore",
            "verifier",
            "health",
            "perf",
            "mods",
        )
        tasks = {name: RecordingTask(name, events) for name in names}
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                rcon_host="minecraft",
                rcon_port=25575,
                rcon_password="test",
                session_secret="test-secret",
                minecraft_container="minecraft",
                mc_log_file="/nonexistent/latest.log",
                rbac_config=os.path.join(directory, "roles.yml"),
                audit_log=os.path.join(directory, "audit.jsonl"),
                cookie_secure=False,
                prometheus_url="http://prometheus:9090",
                metrics_config=os.path.join(directory, "metrics.yml"),
                mc_updater_container="mc-updater",
                ops_file=os.path.join(directory, "ops.json"),
                users_file=os.path.join(directory, "users.json"),
            )
            app = create_app(
                settings,
                service=object(),
                hasher=FakeHasher(),
                scheduler=tasks["backup"],
                player_watcher=tasks["player"],
                tempban_scheduler=tasks["tempban"],
                restart_scheduler=tasks["restart"],
                restore_coordinator=tasks["restore"],
                archive_verifier=tasks["verifier"],
                health_watcher=tasks["health"],
                perf_watcher=tasks["perf"],
                mod_update_checker=tasks["mods"],
                display_names=JsonDisplayNames(
                    os.path.join(directory, "display-names.json")
                ),
                password_overrides=JsonCredentials(
                    os.path.join(directory, "passwords.json")
                ),
            )

            async def run_lifespan():
                async with app.router.lifespan_context(app):
                    events.append("app:running")

            asyncio.run(run_lifespan())

        self.assertEqual(events, [
            *(f"start:{name}" for name in names),
            "app:running",
            *(f"stop:{name}" for name in reversed(names)),
        ])


if __name__ == "__main__":
    unittest.main()
