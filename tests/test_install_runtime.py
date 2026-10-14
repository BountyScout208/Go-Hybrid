#!/usr/bin/env python3
"""Exercise install.sh's generated launcher against an offline release fixture.

The fixture executable only reports its environment and argv. These tests cover
installer/launcher behaviour, not compiler or Rust bridge correctness. No network
access, toolchain download, account, or third-party Python package is required.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherRuntimeTests(unittest.TestCase):
    def setUp(self):
        supported_os = {"Linux": "linux", "Darwin": "darwin"}
        supported_arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
        if platform.system() not in supported_os or platform.machine() not in supported_arch:
            self.skipTest("install.sh supports Linux/macOS on amd64/arm64")
        if not shutil.which("sha256sum") and not shutil.which("shasum"):
            self.skipTest("the installer requires sha256sum or shasum")

        temporary = tempfile.TemporaryDirectory(prefix="hybrid-installer-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.install = self.root / "installed hybrid"
        self.bin_dir = self.root / "launcher bin"
        release = self.root / "release"
        release.mkdir()
        payload = self.root / "hybrid-v0.0.0-test"
        (payload / "runtime").mkdir(parents=True)
        (payload / "runtime" / "fixture.txt").write_text("offline test runtime\n", encoding="utf-8")
        (payload / "hyb").write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "if sys.argv[1:] == ['explain', 'E0002']:\n"
            "    sys.exit(0)\n"
            "print(json.dumps({'runtime': os.environ.get('HYB_RUNTIME_DIR'), 'args': sys.argv[1:]}))\n",
            encoding="utf-8",
        )
        (payload / "hyb").chmod(0o755)
        asset = (
            "hybrid-v0.0.0-test-"
            f"{supported_os[platform.system()]}-{supported_arch[platform.machine()]}.tar.gz"
        )
        with tarfile.open(release / asset, "w:gz") as archive:
            archive.add(payload, arcname=payload.name)
        digest = hashlib.sha256((release / asset).read_bytes()).hexdigest()
        (release / f"{asset}.sha256").write_text(f"{digest}  {asset}\n", encoding="utf-8")

        shim_dir = self.root / "offline tools"
        shim_dir.mkdir()
        (shim_dir / "curl").write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            "prefix = 'https://github.com/limitlessprojects/Go-Hybrid/releases/download/v0.0.0-test/'\n"
            "if len(args) != 4 or args[0] != '-fsSL' or args[2] != '-o' or not args[1].startswith(prefix):\n"
            "    sys.exit('unexpected download request in offline test')\n"
            "name = args[1][len(prefix):]\n"
            "if name != pathlib.Path(name).name:\n"
            "    sys.exit('unexpected asset path in offline test')\n"
            "shutil.copyfile(pathlib.Path(os.environ['HYB_TEST_RELEASE_DIR']) / name, args[3])\n",
            encoding="utf-8",
        )
        (shim_dir / "curl").chmod(0o755)
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("HYB_")
        }
        self.env.update({
            "HOME": str(self.root / "home"),
            "PATH": str(shim_dir) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "HYB_VERSION": "v0.0.0-test",
            "HYB_INSTALL_DIR": str(self.install),
            "HYB_BIN_DIR": str(self.bin_dir),
            "HYB_TEST_RELEASE_DIR": str(release),
        })

    def install_fixture(self, **overrides):
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh")],
            env={**self.env, **overrides},
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("==> sha256 verified", result.stdout)

    def probe(self, *args, **overrides):
        env = dict(self.env)
        # Exercise the baked-in installation location, as a fresh shell does.
        env.pop("HYB_INSTALL_DIR", None)
        env.update(overrides)
        result = subprocess.run(
            [str(self.bin_dir / "hyb"), *args], env=env,
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_unset_runtime_uses_packaged_default(self):
        self.install_fixture()
        self.assertEqual(self.probe()["runtime"], str(self.install / "runtime"))

    def test_empty_runtime_uses_packaged_default(self):
        self.install_fixture()
        self.assertEqual(self.probe(HYB_RUNTIME_DIR="")["runtime"], str(self.install / "runtime"))

    def test_explicit_runtime_is_preserved_verbatim(self):
        self.install_fixture()
        runtime = str(self.root / "custom runtime $literal 'quoted'")
        Path(runtime).mkdir()
        self.assertEqual(self.probe(HYB_RUNTIME_DIR=runtime)["runtime"], runtime)

    def test_runtime_can_change_between_invocations(self):
        self.install_fixture()
        for name in ("runtime one", "runtime two"):
            runtime = str(self.root / name)
            Path(runtime).mkdir()
            with self.subTest(runtime=name):
                self.assertEqual(self.probe(HYB_RUNTIME_DIR=runtime)["runtime"], runtime)

    def test_install_dir_override_sets_default_runtime(self):
        self.install_fixture()
        relocated = self.root / "relocated hybrid"
        shutil.copytree(self.install, relocated)
        self.assertEqual(
            self.probe(HYB_INSTALL_DIR=str(relocated))["runtime"],
            str(relocated / "runtime"),
        )

    def test_explicit_runtime_wins_over_install_dir_override(self):
        self.install_fixture()
        relocated = self.root / "relocated hybrid"
        shutil.copytree(self.install, relocated)
        runtime = str(self.root / "separate runtime")
        Path(runtime).mkdir()
        self.assertEqual(
            self.probe(HYB_INSTALL_DIR=str(relocated), HYB_RUNTIME_DIR=runtime)["runtime"],
            runtime,
        )

    def test_install_time_runtime_is_not_baked_into_launcher(self):
        self.install_fixture(HYB_RUNTIME_DIR=str(self.root / "install-time runtime"))
        self.assertEqual(self.probe()["runtime"], str(self.install / "runtime"))

    def test_arguments_are_forwarded_unchanged(self):
        self.install_fixture()
        args = ["build", "file with spaces.hy", "", "$literal", "a'b"]
        self.assertEqual(self.probe(*args)["args"], args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
