#!/usr/bin/env python3
"""Opt-in remote smoke test: temporary library, tiny file, CPU-only HTTP fixture; removes only its own fixtures."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--models-dir", help="Test transfers in a unique temporary directory on this server drive")
    options = parser.parse_args()
    host = options.host
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        parser.error("Use an SSH host alias")
    identifier = "smoke-" + uuid.uuid4().hex
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", host]
    setup = r'''
import json, pathlib, socket, sys
name = sys.argv[1]
root = pathlib.Path.home()/'.local/share/stabilitymatrix-remote/tests'/name
package = root/'Packages/Fixture'
(package/'venv/bin').mkdir(parents=True)
(package/'venv/bin/python').symlink_to(sys.executable)
(root/'Models/StableDiffusion').mkdir(parents=True)
(package/'main.py').write_text("""import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
p=argparse.ArgumentParser(); p.add_argument('--port',type=int); a,_=p.parse_known_args()
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  data=b'stability-matrix-remote-fixture'
  self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
HTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
""")
(root/'settings.json').write_text(json.dumps({'InstalledPackages':[{'Id':name,'DisplayName':'Temporary remote smoke fixture','PackageName':'ComfyUI','LibraryPath':'Packages/Fixture','LaunchCommand':'main.py'}]}))
with socket.socket() as s:
 s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
print(json.dumps({'root':str(root),'port':port}))
'''
    fixture = json.loads(subprocess.check_output(ssh + ["python3 - " + identifier], input=setup.encode()))
    base = {"library": fixture["root"], "comfy": fixture["root"] + "/absent"}
    drive_fixture = None
    if options.models_dir:
        import shlex
        program = "import pathlib,sys; p=pathlib.Path(sys.argv[1]).expanduser(); assert p.is_dir(); d=p/sys.argv[2]; d.mkdir(); print(d)"
        drive_fixture = subprocess.check_output(ssh + ["python3 -c " + shlex.quote(program) + " " + shlex.quote(options.models_dir) + " " + identifier], text=True).strip()
        base["models"] = drive_fixture
    transfer_root = "downloads" if drive_fixture else "library"
    jobs = []
    started = False

    def request(value, payload=b""):
        command = {**base, **value}
        result = subprocess.run(ssh + ["python3 ~/.local/share/stabilitymatrix-remote/agent.py"],
                                input=json.dumps(command).encode() + b"\n" + payload, capture_output=True)
        response = json.loads(result.stdout)
        if not response["ok"]:
            raise RuntimeError(response["error"])
        return response["data"]

    def job(operation, **args):
        result = request({"action": "job", "operation": operation, **args})
        jobs.append(result["jobId"])
        for _ in range(45):
            data = request({"action": "inventory"})
            state = next((j for j in data["jobs"] if j["id"] == result["jobId"]), {})
            if state.get("status") == "Complete":
                return state
            if state.get("status") in ("Failed", "Interrupted"):
                raise RuntimeError(state.get("message"))
            time.sleep(0.3)
        raise TimeoutError(operation)

    try:
        payload = b"stability-matrix-upload-fixture"
        request({"action": "upload", "root": transfer_root, "path": "StableDiffusion/test.bin", "size": len(payload)}, payload)
        job("move", root=transfer_root, path="StableDiffusion/test.bin", destination="StableDiffusion/moved.bin")
        job("trash", root=transfer_root, path="StableDiffusion/moved.bin")
        job("restore")
        inventory = request({"action": "inventory"})
        assert any(m["path"] == "StableDiffusion/moved.bin" and m["size"] == len(payload) for m in inventory["models"])
        job("start", packageId=identifier, port=fixture["port"])
        started = True
        job("download", path="StableDiffusion/download.bin", sources=[{"url": f"http://127.0.0.1:{fixture['port']}/model.bin"}])
        job("stop", packageId=identifier)
        started = False
        inventory = request({"action": "inventory"})
        assert not next(p for p in inventory["packages"] if p["id"] == identifier)["running"]
        assert any(m["path"] == "StableDiffusion/download.bin" and m["size"] > 0 for m in inventory["models"])
        if drive_fixture:
            for relative in ("StableDiffusion/moved.bin", "StableDiffusion/download.bin"):
                assert any(m["root"] == "downloads" and m["path"] == relative for m in inventory["models"])
            assert not any(m["root"] == "library" for m in inventory["models"])
            print("Verified download/upload destination:", drive_fixture)
        print("PASS: SSH upload, move, trash, restore, detached launch, background HTTP download and graceful stop")
    finally:
        if started:
            job("stop", packageId=identifier)
        cleanup = r'''
import fcntl,json,pathlib,shutil,sys
request=json.loads(sys.stdin.readline()); state=pathlib.Path.home()/'.local/share/stabilitymatrix-remote'
root=pathlib.Path(request['root'])
assert root.parent == state/'tests' and root.name == request['id']
with (state/'mutation.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 launches=state/'launches.json'
 if launches.exists():
  data=json.loads(launches.read_text()); data.pop(request['id'],None); launches.write_text(json.dumps(data))
 for job in request['jobs']:
  for ext in ('.json','.log'): (state/'jobs'/(job+ext)).unlink(missing_ok=True)
 for manifest in (state/'trash').glob('*/manifest.json'):
  if json.loads(manifest.read_text()).get('library') == str(root): shutil.rmtree(manifest.parent)
 (state/('package-'+request['id']+'.log')).unlink(missing_ok=True)
 shutil.rmtree(root)
 if request.get('drive'):
  drive=pathlib.Path(request['drive'])
  assert drive.name == request['id'] and drive.is_dir() and not drive.is_symlink()
  shutil.rmtree(drive)
'''
        # Pass the fixed cleanup program as a quoted command; identifiers stay on stdin.
        import shlex
        subprocess.run(ssh + ["python3 -c " + shlex.quote(cleanup)],
                       input=json.dumps({"root": fixture["root"], "id": identifier, "jobs": jobs, "drive": drive_fixture}).encode(), check=True)


if __name__ == "__main__":
    main()
