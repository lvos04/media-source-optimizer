import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[3] / "scripts" / "check_dockerfile_digests.py"
DIGEST = "sha256:" + "a" * 64
PINNED_IMAGE = f"voorbeeld:1.2.3@{DIGEST}"


class DockerfileDigestTest(unittest.TestCase):
    def check_dockerfile(self, contents: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile = Path(tmpdir) / "Dockerfile"
            dockerfile.write_text(contents, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), str(dockerfile)], capture_output=True, text=True, check=False
            )

    def test_accepts_pinned_images_and_internal_stages(self):
        result = self.check_dockerfile(
            f'ARG BASE_IMAGE="{PINNED_IMAGE}"\n'
            "FROM --platform=$BUILDPLATFORM \\\n"
            "  ${BASE_IMAGE} AS Builder\n"
            f"from {PINNED_IMAGE} AS other\n"
            "FROM builder\n"
            "FROM scratch\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_unpinned_from_line(self):
        result = self.check_dockerfile("# FROM genegeerd:1.0\nFROM voorbeeld:1.2.3\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Dockerfile:2: FROM voorbeeld:1.2.3", result.stderr)

    def test_rejects_image_argument_without_a_valid_digest(self):
        for default in ("", "=voorbeeld:1.2.3", "=voorbeeld:1.2.3@sha256:kort"):
            with self.subTest(default=default):
                result = self.check_dockerfile(f"ARG BASE_IMAGE{default}\nFROM $BASE_IMAGE\n")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Dockerfile:1: ARG BASE_IMAGE", result.stderr)
                self.assertIn("Dockerfile:2: FROM $BASE_IMAGE", result.stderr)

    def test_rejects_from_using_an_argument_without_a_global_pin(self):
        result = self.check_dockerfile(f"FROM {PINNED_IMAGE}\nARG BASE={PINNED_IMAGE}\nFROM $BASE\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Dockerfile:3: FROM $BASE", result.stderr)


if __name__ == "__main__":
    unittest.main()
