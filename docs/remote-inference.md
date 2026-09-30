# Remote generation from the Mac app

This fork keeps Stability Matrix on your Mac and runs Inference and ComfyUI-backed
Image Lab generation on an existing remote ComfyUI server. Input images travel over
HTTP, progress uses WebSockets, and generated images are downloaded into the Mac's
library. No shared filesystem or local ComfyUI installation is needed.

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

The Packages and Models pages continue to manage the Mac's library. Install remote
models and extensions on the server. Missing nodes are reported by ComfyUI when a
workflow is submitted. Image and mask uploads are supported; arbitrary model/config
file transfers are not. Choose models reported by the connected server.

## Build

Install a .NET 9 SDK and follow [the build guide](../CONTRIBUTING.md). For this fork:

```sh
HUSKY=0 bash Build/build_macos_app.sh -v 2.16.0-remote.1 -- -restore \
  -p:Version=2.16.0-remote.1 -p:CFBundleIdentifier=com.nreck.stabilitymatrix.remote
```

The app is written to `out/osx-arm64/Stability Matrix.app`; it can be copied to
`~/Applications/Stability Matrix Remote.app`. Disable automatic upstream update
checks in this installation: an upstream release will not contain these changes.

## Verification

```sh
HUSKY=0 dotnet test StabilityMatrix.Tests -r osx-arm64 -c Release \
  --filter FullyQualifiedName~ComfyEndpointTests
SM_TEST_COMFY_URL=http://127.0.0.1:18188 HUSKY=0 dotnet test \
  StabilityMatrix.Tests -r osx-arm64 -c Release \
  --filter FullyQualifiedName~RemoteComfyIntegrationTests
```

The opt-in integration test connects the real C# ComfyClient, enumerates models
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
