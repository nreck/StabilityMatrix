#!/usr/bin/env python3
"""Stability Matrix remote library operations. JSON over authenticated SSH, no listening port."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

STATE = Path.home() / ".local/share/stabilitymatrix-remote"
WEIGHTS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx"}
CATALOG = {
    "ComfyUI": {"name": "ComfyUI", "repo": "https://github.com/comfyanonymous/ComfyUI.git", "branch": "master", "entry": "main.py", "python": "3.12"},
    "stable-diffusion-webui": {"name": "AUTOMATIC1111", "repo": "https://github.com/AUTOMATIC1111/stable-diffusion-webui.git", "branch": "dev", "entry": "launch.py", "python": "3.10"},
    "forge-neo": {"name": "Forge Neo", "repo": "https://github.com/Haoming02/sd-webui-forge-classic.git", "branch": "neo", "entry": "launch.py", "python": "3.13"},
    "stable-diffusion-webui-forge": {"name": "Forge", "repo": "https://github.com/lllyasviel/stable-diffusion-webui-forge.git", "branch": "main", "entry": "launch.py", "python": "3.10"},
    "forge-classic": {"name": "Forge Classic", "repo": "https://github.com/Haoming02/sd-webui-forge-classic.git", "branch": "classic", "entry": "launch.py", "python": "3.13"},
}


class JobCancelled(Exception):
    pass


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x") as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, indent=2)
    os.replace(temp, path)


def prepare_state():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    (STATE / "jobs").mkdir(exist_ok=True)


@contextlib.contextmanager
def mutation_lock():
    prepare_state()
    with (STATE / "mutation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def library(request):
    path = Path(request.get("library") or "~/Applications/StabilityMatrix/Data").expanduser().resolve()
    if not path.is_dir() or not (path / "settings.json").is_file():
        raise ValueError("Server library must contain Stability Matrix settings.json: " + str(path))
    return path


def roots(request):
    result = {"library": library(request) / "Models"}
    comfy = Path(request.get("comfy") or "~/ComfyUI").expanduser().resolve()
    if (comfy / "main.py").is_file() and (comfy / "models").is_dir():
        result["comfy"] = comfy / "models"
    shared = Path.home() / "comfy/ComfyUI/models"
    if shared.is_dir() and shared.resolve() not in [p.resolve() for p in result.values()]:
        result["sharedComfy"] = shared
    return result


def safe_child(root, relative, require_exists=False):
    parts = str(relative).replace("\\", "/").split("/")
    if not parts or any(p in ("", ".", "..") for p in parts) or str(relative).startswith("/"):
        raise ValueError("Use a relative path without empty, dot, or parent segments")
    root = root.resolve()
    path = root.joinpath(*parts)
    # Never use the management API to follow a directory symlink outside its selected model root.
    if not path.parent.resolve().is_relative_to(root):
        raise ValueError("Path leaves the selected root through a symbolic link")
    if require_exists and not path.exists() and not path.is_symlink():
        raise FileNotFoundError(str(path))
    return path


def model_path(request, key="path", exists=False):
    root = roots(request).get(request.get("root", "library"))
    if root is None:
        raise ValueError("Unknown model location")
    path = safe_child(root, request[key], exists)
    if path.suffix.lower() not in WEIGHTS or path.is_dir():
        raise ValueError("Select a model file, not a directory or metadata file")
    return path


def process_identity(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError):
        return None


def managed_running(record):
    return bool(record and record.get("identity") and process_identity(record["pid"]) == record["identity"])


def external_processes():
    result = {}
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "python" in args and any(x in args for x in ("main.py", "launch.py", "webui.py")):
                words = shlex.split(args)
                port = 0
                if "--port" in words and words.index("--port") + 1 < len(words):
                    with contextlib.suppress(ValueError):
                        port = int(words[words.index("--port") + 1])
                result[str((proc / "cwd").resolve())] = {"pid": int(proc.name), "port": port}
        except OSError:
            continue
    return result


def packages(request):
    root = library(request)
    settings = read_json(root / "settings.json", {})
    result = {}
    for item in settings.get("InstalledPackages", []):
        relative = item.get("LibraryPath")
        if not relative:
            continue
        path = safe_child(root, relative)
        result[str(path)] = {"id": str(item["Id"]), "name": item.get("DisplayName", path.name),
                             "kind": item.get("PackageName", ""), "path": str(path),
                             "entry": item.get("LaunchCommand", "launch.py"),
                             "launchArgs": item.get("LaunchArgs") or [],
                             "environment": item.get("EnvironmentVariables") or {}}
    for item in read_json(STATE / "packages.json", []):
        if item.get("library") == str(root):
            result[item["path"]] = item
    comfy = Path(request.get("comfy") or "~/ComfyUI").expanduser().resolve()
    if (comfy / "main.py").is_file() and str(comfy) not in result:
        result[str(comfy)] = {"id": "external-comfy", "name": "ComfyUI (inference server)",
                              "kind": "ComfyUI", "path": str(comfy), "entry": "main.py"}
    external = external_processes()
    launches = read_json(STATE / "launches.json", {})
    for item in result.values():
        launch = launches.get(item["id"])
        managed = managed_running(launch)
        item["managed"] = managed
        item["running"] = managed or item["path"] in external
        item["status"] = "Running (remote manager)" if managed else "Running (existing process)" if item["running"] else "Stopped"
        saved_port = next((a.get("OptionValue") for a in item.get("launchArgs", []) if a.get("Name") == "--port" and a.get("OptionValue")), None)
        item["port"] = launch.get("port", 0) if managed else external.get(item["path"], {}).get("port", 0)
        if not item["port"]:
            item["port"] = int(saved_port or (8196 if item["kind"] == "ComfyUI" else 7860))
        # Never send configured environment variables (which can contain tokens) to the client.
    return list(result.values())


def get_package(request):
    for package in packages(request):
        if package["id"] == request.get("packageId"):
            return package
    raise ValueError("Package is no longer present; refresh the server inventory")


def models(request):
    result = []
    for key, root in roots(request).items():
        if not root.exists():
            continue
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                path = Path(directory) / name
                if path.suffix.lower() not in WEIGHTS:
                    continue
                try:
                    relative = path.relative_to(root).as_posix()
                    result.append({"root": key, "path": relative, "name": name,
                                   "category": relative.split("/")[0] if "/" in relative else "Other",
                                   "size": path.stat().st_size})
                except OSError:
                    continue
    return sorted(result, key=lambda m: (m["root"], m["path"].lower()))


def job_summary(path):
    job = read_json(path, {})
    if job.get("status") in ("Running", "Queued") and not managed_running(job):
        # A job is written before its worker is spawned; allow that small startup window.
        if time.time() - job.get("created", 0) > 10:
            job["status"] = "Interrupted"
            job["message"] = "Worker exited unexpectedly; inspect the job log before retrying."
    return {k: job.get(k, "") for k in ("id", "description", "status", "message", "created")}


def inventory(request):
    items = packages(request)
    public = [{k: v for k, v in p.items() if k not in ("environment", "launchArgs")} for p in items]
    jobs = sorted((STATE / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
    return {"host": socket.gethostname(), "library": str(library(request)), "packages": public,
            "models": models(request), "jobs": [job_summary(p) for p in jobs],
            "catalog": [{"id": k, "name": v["name"]} for k, v in CATALOG.items()],
            "roots": [{"id": k, "name": "Stability Matrix library" if k == "library" else "Inference ComfyUI" if k == "comfy" else "Shared ComfyUI", "path": str(v)} for k, v in roots(request).items()],
            "freeBytes": shutil.disk_usage(library(request)).free}


def run_command(args, cwd):
    print("Running:", shlex.join([str(a) for a in args]), flush=True)
    subprocess.run([str(a) for a in args], cwd=cwd, check=True, stdin=subprocess.DEVNULL)


def python_for(package):
    path = Path(package["path"])
    for relative in ("venv/bin/python", ".venv/bin/python"):
        candidate = path / relative
        if candidate.exists():
            return candidate
    raise ValueError("Package has no venv/bin/python or .venv/bin/python; repair its environment on the server")


def shared_paths(package, request):
    if package["kind"] != "ComfyUI":
        return
    mapping = {"checkpoints": "StableDiffusion", "loras": "Lora", "vae": "VAE",
               "text_encoders": "TextEncoders", "diffusion_models": "DiffusionModels",
               "controlnet": "ControlNet", "clip_vision": "ClipVision", "upscale_models": "ESRGAN",
               "embeddings": "Embeddings"}
    # Keep any user-owned extra_model_paths.yaml untouched; pass this separate file at launch.
    file = Path(package["path"]) / "stability_matrix_remote_paths.yaml"
    root = library(request) / "Models"
    text = "stability_matrix_remote:\n" + "".join(f"  {key}: {json.dumps(str(root / value))}\n" for key, value in mapping.items())
    file.write_text(text)
    return file


def install_package(request):
    adapter = CATALOG.get(request.get("kind"))
    if not adapter:
        raise ValueError("Choose a supported package from the install catalog")
    name = request.get("name", "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError("Enter a folder name without slashes")
    root = library(request)
    target = safe_child(root, "Packages/" + name)
    if target.exists():
        raise FileExistsError("Package folder already exists; no files were changed")
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    if not Path(uv).is_file():
        raise ValueError("Install uv on the server first; remote installation uses uv-managed Python")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_command(["git", "clone", "--branch", adapter["branch"], adapter["repo"], target], root)
    run_command([uv, "venv", "--seed", "--python", adapter["python"], str(target / "venv")], target)
    package = {"id": str(uuid.uuid4()), "name": name, "kind": request["kind"], "path": str(target),
               "entry": adapter["entry"], "library": str(root)}
    python = python_for(package)
    if package["kind"] == "ComfyUI":
        run_command([python, "-m", "pip", "install", "-r", "requirements.txt"], target)
        shared_paths(package, request)
    else:
        # These upstream launchers own their version-specific torch/repository setup.
        run_command([python, "launch.py", "--exit", "--skip-torch-cuda-test"], target)
    registry = read_json(STATE / "packages.json", [])
    registry.append(package)
    write_json(STATE / "packages.json", registry)
    return "Installed " + name


def update_package(request):
    package = get_package(request)
    if package["running"]:
        raise ValueError("Stop this package before updating it")
    cwd = Path(package["path"])
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=cwd, text=True)
    if dirty.strip():
        raise ValueError("Package has local source changes; update refused to preserve them")
    if package["kind"] not in CATALOG:
        raise ValueError("Dependency updates are not supported for this package type")
    run_command(["git", "pull", "--ff-only"], cwd)
    python = python_for(package)
    if package["kind"] == "ComfyUI":
        run_command([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd)
    elif package["kind"] in CATALOG:
        run_command([python, "launch.py", "--exit", "--skip-torch-cuda-test"], cwd)
    else:
        raise ValueError("Dependency updates are not supported for this package type")
    return "Updated " + package["name"]


def start_package(request):
    package = get_package(request)
    if package["running"]:
        return "Already running; existing process left unchanged"
    if package["kind"] not in CATALOG:
        raise ValueError("This package type has no remote launch adapter")
    port = int(request.get("port", 8196))
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    cwd = Path(package["path"])
    # Preserve non-network launch flags, overriding only the bind address and port.
    args = []
    for item in package.get("launchArgs", []):
        name, value = item.get("Name", ""), item.get("OptionValue")
        if not name or not value:
            continue
        words = shlex.split(name)
        if any(w in ("--listen", "--server-name", "--port", "--share", "--enable-insecure-extension-access") for w in words):
            continue
        args.extend(words)
        if not isinstance(value, bool):
            args.append(str(value))
    if package["kind"] == "ComfyUI":
        config = shared_paths(package, request)
        args += ["--listen", "127.0.0.1", "--port", str(port), "--extra-model-paths-config", str(config)]
    else:
        args += ["--server-name", "127.0.0.1", "--port", str(port), "--ckpt-dir", str(library(request) / "Models/StableDiffusion")]
    args += shlex.split(request.get("arguments", ""))
    # Custom arguments cannot make the service publicly accessible.
    if any(arg.split("=", 1)[0] in ("--share", "--listen", "--server-name", "--port") for arg in shlex.split(request.get("arguments", ""))):
        raise ValueError("Configure the port using the Port field; network/share flags are not accepted")
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in package.get("environment", {}).items()})
    gpu = request.get("gpu", "").strip()
    if gpu:
        if not gpu.isdecimal():
            raise ValueError("GPU must be an index such as 0 or 1")
        env["CUDA_VISIBLE_DEVICES"] = gpu
    logpath = STATE / ("package-" + package["id"] + ".log")
    with logpath.open("ab") as log:
        child = subprocess.Popen([str(python_for(package)), package["entry"], *args], cwd=cwd,
                                 env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
    launches = read_json(STATE / "launches.json", {})
    launches[package["id"]] = {"pid": child.pid, "identity": process_identity(child.pid), "port": port}
    write_json(STATE / "launches.json", launches)
    time.sleep(1)
    if child.poll() is not None:
        raise RuntimeError("Package exited during startup. Open Package logs for details.")
    return "Started " + package["name"] + f" on server localhost:{port}; check logs for readiness"


def stop_package(request):
    package = get_package(request)
    launches = read_json(STATE / "launches.json", {})
    record = launches.get(package["id"])
    if not managed_running(record):
        raise ValueError("This process was not started by the remote manager; stop it with its original launcher")
    os.killpg(record["pid"], signal.SIGTERM)
    for _ in range(100):
        if not managed_running(record):
            return "Stopped " + package["name"]
        time.sleep(0.1)
    raise RuntimeError("Process has not exited after SIGTERM; inspect the server before retrying")


def sidecars(path):
    candidates = [path]
    for suffix in (".cm-info.json", ".preview.png", ".preview.jpeg", ".preview.jpg", ".preview.webp", ".sha256", ".yaml"):
        extra = path.with_name(path.stem + suffix)
        if extra.exists():
            candidates.append(extra)
    return candidates


def move_model(request):
    source = model_path(request, exists=True)
    target = model_path(request, "destination")
    if source == target:
        return "Path unchanged"
    if target.suffix.lower() not in WEIGHTS:
        raise ValueError("Keep a supported model file extension")
    pairs = [(p, target if p == source else target.with_name(target.stem + p.name[len(source.stem):])) for p in sidecars(source)]
    if any(t.exists() or t.is_symlink() for _, t in pairs):
        raise FileExistsError("Destination or a metadata sidecar already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    moved = []
    try:
        for old, new in pairs:
            shutil.move(str(old), str(new)); moved.append((old, new))
    except Exception:
        for old, new in reversed(moved):
            shutil.move(str(new), str(old))
        raise
    return "Moved model and metadata"


def trash_model(request):
    source = model_path(request, exists=True)
    folder = STATE / "trash" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    items = [{"original": str(p), "stored": str(folder / p.name)} for p in sidecars(source)]
    write_json(folder / "manifest.json", {"library": str(library(request)), "items": items, "created": time.time()})
    for item in items:
        shutil.move(item["original"], item["stored"])
    return "Moved to server trash; use Restore last trashed model to undo"


def restore_model(request):
    entries = []
    for path in (STATE / "trash").glob("*/manifest.json"):
        item = read_json(path)
        if item["library"] == str(library(request)) and not item.get("restored"):
            entries.append((path, item))
    if not entries:
        raise ValueError("No trashed models to restore for this library")
    path, manifest = max(entries, key=lambda item: item[1]["created"])
    if any(Path(p["original"]).exists() or Path(p["original"]).is_symlink() for p in manifest["items"]):
        raise FileExistsError("Original path is occupied; restore would overwrite a file")
    for item in manifest["items"]:
        if Path(item["stored"]).exists() or Path(item["stored"]).is_symlink():
            Path(item["original"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(item["stored"], item["original"])
    manifest["restored"] = True
    write_json(path, manifest)
    return "Restored model and metadata"


def save_stream(request, stream, expected_size=None):
    target = model_path(request)
    if target.suffix.lower() not in WEIGHTS:
        raise ValueError("Choose a model filename (.safetensors, .gguf, .ckpt, .pt, .pth, .bin or .onnx)")
    if target.exists() or target.is_symlink():
        raise FileExistsError("Destination exists; downloads/uploads never overwrite models")
    target.parent.mkdir(parents=True, exist_ok=True)
    if expected_size is not None:
        if expected_size <= 0:
            raise ValueError("File size must be positive")
        if shutil.disk_usage(target.parent).free < expected_size + 256 * 1024 * 1024:
            raise ValueError("Not enough server disk space for this model")
    temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    digest = hashlib.sha256()
    total = 0
    try:
        with temp.open("xb") as output:
            while True:
                chunk = stream.read(min(1024 * 1024, expected_size - total) if expected_size is not None else 1024 * 1024)
                if not chunk:
                    break
                output.write(chunk); digest.update(chunk); total += len(chunk)
        if expected_size is not None and total != expected_size:
            raise ValueError("Transfer ended before the declared file size")
        if not total:
            raise ValueError("Empty model file")
        if request.get("sha256") and digest.hexdigest().lower() != request["sha256"].lower().strip():
            raise ValueError("SHA-256 verification failed")
        os.link(temp, target)  # Atomic no-overwrite publish on the same filesystem.
    finally:
        temp.unlink(missing_ok=True)
    return f"Saved {target.name} ({total} bytes)"


def download_model(request):
    url = request.get("url", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Enter a direct HTTP(S) model download URL")
    headers = {"User-Agent": "StabilityMatrixRemote/1"}
    if request.get("token"):
        headers["Authorization"] = "Bearer " + request["token"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        if "text/html" in response.headers.get("Content-Type", ""):
            raise ValueError("URL returned a web page; use a direct model file URL")
        size = response.headers.get("Content-Length")
        return save_stream(request, response, int(size) if size else None)


OPERATIONS = {"install": install_package, "update": update_package, "start": start_package,
              "stop": stop_package, "move": move_model, "trash": trash_model,
              "restore": restore_model, "download": download_model}


def start_job(request):
    if request.get("operation") not in OPERATIONS:
        raise ValueError("Unknown job operation")
    library(request)
    identifier = uuid.uuid4().hex
    path = STATE / "jobs" / (identifier + ".json")
    job = {"id": identifier, "description": request["operation"] + ": " + request.get("name", request.get("path", request.get("packageId", "model"))),
           "status": "Queued", "created": time.time(), "request": request}
    write_json(path, job)
    with path.with_suffix(".log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "worker", identifier],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    # Worker records its own identity; do not race its status by rewriting this file here.
    return {"message": "Queued remote job " + identifier[:8], "jobId": identifier}


def worker(identifier):
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid job ID")
    path = STATE / "jobs" / (identifier + ".json")
    def cancel(*_):
        raise JobCancelled("Cancelled by user; completed files and partial package folders are preserved")
    signal.signal(signal.SIGTERM, cancel)
    job = read_json(path)
    job.update(pid=os.getpid(), identity=process_identity(os.getpid()))
    write_json(path, job)
    try:
        with mutation_lock():
            job["status"] = "Running"; write_json(path, job)
            job["message"] = OPERATIONS[job["request"]["operation"]](job["request"])
            job["status"] = "Complete"
    except JobCancelled as error:
        job["status"] = "Cancelled"; job["message"] = str(error)
    except Exception as error:
        job["status"] = "Failed"; job["message"] = str(error)
        print(type(error).__name__ + ": " + str(error), flush=True)
    finally:
        # Remove download credentials and URL after completion, including failures.
        job.pop("request", None)
        write_json(path, job)


def dispatch(request, input_stream=None):
    prepare_state()
    action = request.get("action")
    if action == "inventory":
        return inventory(request)
    if action == "job":
        return start_job(request)
    if action == "upload":
        with mutation_lock():
            return {"message": save_stream(request, input_stream, int(request["size"]))}
    if action == "cancel":
        identifier = request.get("jobId", "")
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Invalid job ID")
        record = read_json(STATE / "jobs" / (identifier + ".json"), {})
        if record.get("status") not in ("Queued", "Running") or not managed_running(record):
            raise ValueError("This job is no longer running")
        os.killpg(record["pid"], signal.SIGTERM)
        return {"message": "Cancellation requested"}
    if action == "logs":
        if request.get("packageId"):
            package = get_package(request)
            path = STATE / ("package-" + package["id"] + ".log")
        else:
            identifier = request.get("jobId", "")
            if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
                raise ValueError("Invalid job ID")
            path = STATE / "jobs" / (identifier + ".log")
        if not path.exists():
            return {"message": "No remote-manager log exists for this item yet."}
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 24000))
            return {"message": stream.read().decode(errors="replace")}
    raise ValueError("Unknown action")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "worker":
        worker(sys.argv[2])
    else:
        try:
            request = json.loads(sys.stdin.buffer.readline(65536))
            print(json.dumps({"ok": True, "data": dispatch(request, sys.stdin.buffer)}))
        except Exception as error:
            print(json.dumps({"ok": False, "error": str(error)}))
            sys.exit(1)
