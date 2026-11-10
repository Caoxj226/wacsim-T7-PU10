from __future__ import annotations

import unittest
from pathlib import Path

from reproduction.scripts import run_wacsim_frozen_rq as launcher


class FrozenLauncherTests(unittest.TestCase):
    def test_materializes_digest_and_verified_mount(self) -> None:
        manifest = {
            "source_identity": {"docker_image": {"reference": "image:tag", "repo_digest": "image@sha256:digest"}},
            "rq_commands": {"rq1": {"status": "frozen_not_executed", "argv": ["docker", "run", "<mount>", "image:tag"]}},
        }
        command = launcher._materialize_command(manifest, "rq1", Path("C:/output"))
        self.assertEqual(
            command,
            ["docker", "run", "--mount", f"type=bind,source={Path('C:/output')},target=/opt/crave/results", "image@sha256:digest"],
        )

    def test_rejects_input_outside_repository(self) -> None:
        with self.assertRaises(launcher.PreflightError):
            launcher._checked_path(Path("C:/repo"), "../escape.json")

    def test_requires_frozen_command_status(self) -> None:
        manifest = {
            "source_identity": {"docker_image": {"reference": "image:tag", "repo_digest": "image@sha256:digest"}},
            "rq_commands": {"rq1": {"status": "executed", "argv": ["docker", "run", "<mount>", "image:tag"]}},
        }
        with self.assertRaises(launcher.PreflightError):
            launcher._materialize_command(manifest, "rq1", Path("C:/output"))
