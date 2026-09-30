#!/usr/bin/env python3
"""Install a reconnecting, loopback-only SSH tunnel for remote ComfyUI on macOS."""
import argparse
import os
import pathlib
import plistlib
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", nargs="?", default="servivor", help="Existing SSH config host")
    parser.add_argument("--local-port", type=int, default=18188)
    parser.add_argument("--remote-port", type=int, default=8188)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This installer requires macOS and launchd")
    if args.host.startswith("-") or not args.host or any(c.isspace() for c in args.host):
        parser.error("Host must be an SSH hostname or config alias")
    if not all(1 <= port <= 65535 for port in (args.local_port, args.remote_port)):
        parser.error("Ports must be between 1 and 65535")

    # Check authentication before installing a service that would otherwise retry forever.
    subprocess.run(["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    args.host, "true"], check=True)
    label = "local.stabilitymatrix.remote-tunnel"
    home = pathlib.Path.home()
    plist = home / "Library/LaunchAgents" / (label + ".plist")
    logs = home / "Library/Logs/StabilityMatrix"
    plist.parent.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": label,
        "ProgramArguments": ["/usr/bin/ssh", "-N", "-T", "-o", "BatchMode=yes",
                             "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
                             "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                             "-L", f"127.0.0.1:{args.local_port}:127.0.0.1:{args.remote_port}",
                             args.host],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "StandardErrorPath": str(logs / "remote-tunnel.log"),
    }
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{label}"
    if subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", service], check=True)
    with plist.open("wb") as stream:
        plistlib.dump(config, stream)
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print(f"Tunnel installed: http://127.0.0.1:{args.local_port} -> {args.host}:{args.remote_port}")
    print(f"Logs: {logs / 'remote-tunnel.log'}")


if __name__ == "__main__":
    main()
