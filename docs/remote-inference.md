# Remote generation and library management from the Mac app

This fork keeps Stability Matrix on your Mac and runs Inference and ComfyUI-backed
Image Lab generation on an existing remote ComfyUI server. Input images travel over
HTTP, progress uses WebSockets, and generated images are downloaded into the Mac's
library. Packages and Checkpoint Manager also manage the server over SSH. No shared
filesystem or local ComfyUI installation is needed.

## servivor setup

The server must already have ComfyUI running on `127.0.0.1:8188`, with its models
and custom nodes installed. Use the existing `servivor` SSH configuration and key.
The tunnel does not install, restart, or stop server workloads.

On the Mac, run from this repository:

```sh
python3 Tools/remote/install-macos-tunnel.py servivor
curl --fail http://127.0.0.1:18188/system_stats
```

The installer checks SSH authentication, installs a user LaunchAgent, and starts
the tunnel immediately. It reconnects after network interruptions and starts again
at login. Both ends bind only to loopback; no ComfyUI public port is needed. To use
different ports, pass `--local-port` and `--remote-port`.

In **Settings → Inference → Remote generation**, enable **Use a remote ComfyUI
server**, set the address to `http://127.0.0.1:18188`, and click **Connect** on
Inference or Image Lab. Disconnect before changing servers. Turn the toggle off
to resume local operation. HTTP and HTTPS root URLs are supported; HTTPS uses WSS.
Reverse-proxy subpaths and URL-embedded credentials are not supported.

In the same settings section, configure:

| Setting | servivor value |
| --- | --- |
| SSH host | `servivor` |
| Server library | `~/Applications/StabilityMatrix/Data` |
| Existing ComfyUI directory | `~/ComfyUI` |

SSH must authenticate without an interactive prompt. The app deploys its bundled
Python helper to `~/.local/share/stabilitymatrix-remote/agent.py` automatically.
Management uses SSH directly and does not open another server port.

## Packages and models

With remote mode enabled, **Packages** reads the server's existing Stability Matrix
library plus the separate ComfyUI directory. Use Refresh to reload the inventory.
Select a package to launch, update, inspect logs, or open its Web UI through a private
SSH tunnel. Launch options include port, GPU index, and extra arguments. Stop only
stops processes launched by this remote manager; existing processes must be stopped
through their original launcher. Updates refuse running packages and repositories
with modified tracked source files.

The remote installer supports ComfyUI, AUTOMATIC1111, Forge, Forge Classic, and
Forge Neo. It uses the server's `uv` to create the appropriate Python environment
and installs dependencies. Other upstream package types are not supported by the
remote installer. Newly installed packages are recorded in the remote helper's own
registry, leaving the server GUI's settings intact. Failed installs preserve their
partial folder for inspection; retry with a new name or remove that folder manually.

**Use for Inference** connects to the selected ComfyUI package through a session
tunnel. The configured Remote generation URL remains the default for subsequent
connections and app restarts. A newly started backend may take time to become ready.

**Checkpoint Manager** lists models from the server library, the configured ComfyUI
models directory, and `~/comfy/ComfyUI/models` when present. It supports search,
rename/move within a root, reversible trash and restore, direct server downloads,
and uploads from the Mac. Downloads accept an optional bearer token and SHA-256
checksum. Choose a destination root and relative model path; existing files are
never overwritten. Related metadata and preview files move with the model.

The upstream CivitAI/Hugging Face Model Browser still targets the Mac library.
For remote model installation, use Checkpoint Manager's download/upload controls.
Install custom nodes on the server; missing nodes are reported by ComfyUI when a
workflow is submitted. Choose inference models reported by the connected backend;
a newly added model may require a backend refresh or restart before it is available.

Package actions and server downloads run as persistent jobs. Closing the Mac app
does not stop them. Expand Jobs and logs to inspect progress or cancel a selected
job. Cancelled installs can leave partial package folders; incomplete model
transfers are cleaned up. Uploads stream from the Mac and require its connection.
Helper state, logs, package records, and reversible trash are kept under
`~/.local/share/stabilitymatrix-remote/` on the server.

## Build

Install a .NET 9 SDK and follow [the build guide](../CONTRIBUTING.md). For this fork:

```sh
HUSKY=0 bash Build/build_macos_app.sh -v 2.16.0-remote.2 -- -restore \
  -p:Version=2.16.0-remote.2 -p:CFBundleIdentifier=com.nreck.stabilitymatrix.remote
python3 Tools/remote/install-macos-app.py --install
```

Quit the remote app before installation. The installer signs the build locally,
backs up the previous remote app, and installs `/Applications/Stability Matrix Remote.app`.
Its launcher selects `~/Documents/StabilityMatrix-Remote-Data` as the Mac library.
Without `--install`, the installer only stages a signed bundle in `out/osx-arm64/`.
Disable automatic upstream update checks in this installation: an upstream release
will not contain these changes.

## Verification

```sh
python3 -m unittest discover -s Tools/remote -p 'test_*.py'
python3 Tools/remote/smoke_remote_library.py servivor
SM_TEST_REMOTE_SSH=servivor HUSKY=0 dotnet test StabilityMatrix.Tests -r osx-arm64 -c Release \
  --filter FullyQualifiedName~RemoteLibraryTransportTests
HUSKY=0 dotnet test StabilityMatrix.UITests -r osx-arm64 -c Release \
  --filter FullyQualifiedName~RemoteLibraryViewTests
HUSKY=0 dotnet test StabilityMatrix.Tests -r osx-arm64 -c Release \
  --filter FullyQualifiedName~ComfyEndpointTests
SM_TEST_COMFY_URL=http://127.0.0.1:18188 HUSKY=0 dotnet test \
  StabilityMatrix.Tests -r osx-arm64 -c Release \
  --filter FullyQualifiedName~RemoteComfyIntegrationTests
```

The library smoke test creates a temporary server library and a small HTTP fixture,
tests file operations and process lifecycle, then removes its fixtures. It does not
install a real GPU package or modify existing models. The C# transport test reads
the real inventory and verifies an SSH tunnel. Headless UI tests render both remote
pages and check bindings. Full GPU package installation is not covered by these tests.

The opt-in Comfy integration test connects the real C# ComfyClient, enumerates models
and samplers, uploads a 16×16 PNG, queues LoadImage → SaveImage, waits for WebSocket
completion, and downloads and checks the output pixels. It writes one tiny input
and output on the server. It does not load a GPU model or interrupt other jobs.

## Troubleshooting and removal

Tunnel logs: `~/Library/Logs/StabilityMatrix/remote-tunnel.log`.

```sh
launchctl print gui/$(id -u)/local.stabilitymatrix.remote-tunnel
ssh servivor 'curl --fail http://127.0.0.1:8188/system_stats'
```

If SSH works but ComfyUI does not respond, start ComfyUI on the server using its
normal launcher. A tunnel does not keep the backend process running.

To stop the tunnel and prevent it from starting at login:

```sh
launchctl bootout gui/$(id -u)/local.stabilitymatrix.remote-tunnel
mv ~/Library/LaunchAgents/local.stabilitymatrix.remote-tunnel.plist ~/.Trash/
```
