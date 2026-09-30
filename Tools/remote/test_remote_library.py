"""Run with python3 -m unittest discover -s Tools/remote -p 'test_*.py'. No real models are modified."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "StabilityMatrix.Avalonia/Assets/remote_library_agent.py"
spec = importlib.util.spec_from_file_location("remote_library_agent", SOURCE)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class RemoteLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        agent.STATE = self.root / "state"
        agent.prepare_state()
        self.library = self.root / "library"
        self.models = self.library / "Models"
        (self.models / "StableDiffusion").mkdir(parents=True)
        (self.library / "settings.json").write_text('{"InstalledPackages": []}')
        self.request = {"library": str(self.library), "comfy": str(self.root / "absent"),
                        "root": "library", "path": "StableDiffusion/test.safetensors"}

    def tearDown(self):
        self.temp.cleanup()

    def model(self):
        path = self.models / self.request["path"]
        path.write_bytes(b"test-model-data")
        path.with_name("test.cm-info.json").write_text('{"title":"test"}')
        return path

    def test_inventory_finds_models_and_packages_without_leaking_environment(self):
        path = self.model()
        package = self.library / "Packages/ComfyUI"
        package.mkdir(parents=True)
        (self.library / "settings.json").write_text(json.dumps({"InstalledPackages": [{
            "Id": "test-id", "DisplayName": "Test ComfyUI", "PackageName": "ComfyUI",
            "LibraryPath": "Packages/ComfyUI", "LaunchCommand": "main.py",
            "EnvironmentVariables": {"SECRET": "do-not-return"}}]}))
        data = agent.inventory(self.request)
        self.assertEqual(data["models"][0]["size"], path.stat().st_size)
        self.assertEqual(data["packages"][0]["name"], "Test ComfyUI")
        self.assertNotIn("do-not-return", json.dumps(data))

    def test_rejects_traversal_and_absolute_paths(self):
        for relative in ("../escape.bin", "/tmp/escape.bin", "StableDiffusion/../../escape.bin", "./a.bin", "a//b.bin"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                agent.safe_child(self.models, relative)

    def test_rejects_directory_symlink_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.models / "escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            agent.safe_child(self.models, "escape/model.bin")

    def test_move_preserves_metadata_and_checks_collisions(self):
        source = self.model()
        agent.move_model(dict(self.request, destination="StableDiffusion/renamed.safetensors"))
        self.assertFalse(source.exists())
        self.assertTrue(source.with_name("renamed.cm-info.json").exists())
        source.write_bytes(b"occupied")
        with self.assertRaises(FileExistsError):
            agent.move_model(dict(self.request, path="StableDiffusion/renamed.safetensors", destination=self.request["path"]))
        self.assertEqual(source.read_bytes(), b"occupied")

    def test_trash_and_restore_round_trip(self):
        path = self.model()
        agent.trash_model(self.request)
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name("test.cm-info.json").exists())
        agent.restore_model(self.request)
        self.assertEqual(path.read_bytes(), b"test-model-data")
        self.assertTrue(path.with_name("test.cm-info.json").exists())

    def test_restore_does_not_overwrite_new_file(self):
        path = self.model()
        agent.trash_model(self.request)
        path.write_bytes(b"new-model")
        with self.assertRaises(FileExistsError):
            agent.restore_model(self.request)
        self.assertEqual(path.read_bytes(), b"new-model")

    def test_upload_verifies_size_and_hash(self):
        data = b"streamed-model"
        request = dict(self.request, sha256=hashlib.sha256(data).hexdigest())
        agent.save_stream(request, io.BytesIO(data), len(data))
        self.assertEqual((self.models / self.request["path"]).read_bytes(), data)
        with self.assertRaises(FileExistsError):
            agent.save_stream(request, io.BytesIO(data), len(data))

    def test_failed_upload_leaves_no_partial_model(self):
        for request, data, size in [(self.request, b"short", 100), (dict(self.request, sha256="0"*64), b"bad", 3)]:
            with self.assertRaises(ValueError):
                agent.save_stream(request, io.BytesIO(data), size)
            self.assertFalse((self.models / self.request["path"]).exists())
            self.assertEqual(list(self.models.rglob("*.part")), [])

    def test_update_refuses_dirty_repo_before_pulling(self):
        package = {"id": "test", "name": "Test", "kind": "ComfyUI", "path": str(self.root), "running": False}
        with patch.object(agent, "get_package", return_value=package), patch.object(agent.subprocess, "check_output", return_value=" M main.py"), patch.object(agent, "run_command") as run:
            with self.assertRaisesRegex(ValueError, "local source changes"):
                agent.update_package(self.request)
            run.assert_not_called()

    def test_stop_never_signals_unmanaged_process(self):
        with patch.object(agent, "get_package", return_value={"id": "external", "name": "Other"}), patch.object(agent.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "original launcher"):
                agent.stop_package(self.request)
            kill.assert_not_called()

    def test_install_prepares_comfy_and_registers_it(self):
        calls = []
        def command(args, cwd):
            calls.append([str(a) for a in args])
            if args[0] == "git":
                Path(args[-1]).mkdir(parents=True)
            elif args[1] == "venv":
                python = Path(args[-1]) / "bin/python"
                python.parent.mkdir(parents=True)
                python.touch()
        with patch.object(agent.shutil, "which", return_value=sys.executable), patch.object(agent, "run_command", side_effect=command):
            agent.install_package(dict(self.request, kind="ComfyUI", name="NewComfy"))
        self.assertEqual(len(calls), 3)
        self.assertIn("requirements.txt", calls[-1])
        registry = agent.read_json(agent.STATE / "packages.json")
        self.assertEqual(registry[0]["name"], "NewComfy")
        self.assertTrue((self.library / "Packages/NewComfy/stability_matrix_remote_paths.yaml").exists())

    def test_agent_cli_returns_structured_error(self):
        result = subprocess.run([sys.executable, str(SOURCE)], input='{"action":"no-such-action"}\n', text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_cancel_refuses_stale_process_identity(self):
        identifier = "a" * 32
        agent.write_json(agent.STATE / "jobs" / (identifier + ".json"),
                         {"status": "Running", "pid": 123, "identity": "old"})
        with patch.object(agent, "managed_running", return_value=False), patch.object(agent.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "no longer running"):
                agent.dispatch({"action": "cancel", "jobId": identifier})
            kill.assert_not_called()

    def test_new_download_root_keeps_existing_models_and_receives_transfers(self):
        old = self.model()
        drive = self.root / "tt/Models"
        drive.mkdir(parents=True)
        request = dict(self.request, models=str(drive))
        request.pop("root")
        agent.save_stream(request, io.BytesIO(b"new-drive-model"), 15)
        self.assertEqual((drive / request["path"]).read_bytes(), b"new-drive-model")
        self.assertEqual(old.read_bytes(), b"test-model-data")
        inventory = agent.inventory(request)
        self.assertEqual(inventory["roots"][0]["id"], "downloads")
        self.assertEqual({model["root"] for model in inventory["models"]}, {"downloads", "library"})

    def test_missing_download_drive_never_falls_back_to_home(self):
        request = dict(self.request, models=str(self.root / "unmounted/Models"))
        request.pop("root")
        with self.assertRaisesRegex(ValueError, "directory is unavailable"):
            agent.save_stream(request, io.BytesIO(b"model"), 5)
        self.assertFalse((self.models / request["path"]).exists())
        self.assertFalse((self.root / "unmounted").exists())

    def test_model_configuration_files_transfer_but_executables_are_rejected(self):
        for name in ("config.json", "model.yaml"):
            agent.save_stream(dict(self.request, path="TextEncoders/example/" + name), io.BytesIO(b"{}"), 2)
            self.assertTrue((self.models / "TextEncoders/example" / name).is_file())
        with self.assertRaises(ValueError):
            agent.save_stream(dict(self.request, path="TextEncoders/example/run.py"), io.BytesIO(b"print(1)"), 8)

    def test_comfy_configuration_includes_existing_and_download_models(self):
        drive = self.root / "tt/Models"
        drive.mkdir(parents=True)
        package = self.root / "comfy"
        package.mkdir()
        result = agent.shared_paths({"kind": "ComfyUI", "path": str(package)}, dict(self.request, models=str(drive)))
        self.assertIn(str(drive / "StableDiffusion"), result.read_text())
        self.assertIn(str(self.models / "StableDiffusion"), result.read_text())

    def test_redirect_never_sends_account_token_to_other_host(self):
        request = agent.urllib.request.Request("https://civitai.com/file", headers={"Authorization": "Bearer secret"})
        redirected = agent.SafeDownloadRedirect().redirect_request(request, None, 302, "Found", {}, "https://cdn.example/file")
        self.assertFalse(redirected.has_header("Authorization"))

    def test_cancelled_worker_removes_credentials_and_partial_download(self):
        identifier = "b" * 32
        request = dict(self.request, operation="download", token="secret")
        path = agent.STATE / "jobs" / (identifier + ".json")
        agent.write_json(path, {"status": "Queued", "request": request})
        class InterruptedStream:
            def read(self, size):
                raise agent.JobCancelled("Cancelled")
        def download(request):
            return agent.save_stream(request, InterruptedStream())
        with patch.dict(agent.OPERATIONS, download=download), patch.object(agent.signal, "signal"), patch.object(agent, "process_identity", return_value="test"):
            agent.worker(identifier)
        result = agent.read_json(path)
        self.assertEqual(result["status"], "Cancelled")
        self.assertNotIn("request", result)
        self.assertEqual(list(self.models.rglob("*.part")), [])
        self.assertFalse((self.models / self.request["path"]).exists())


if __name__ == "__main__":
    unittest.main()
