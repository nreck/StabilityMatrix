#!/usr/bin/env python3
"""Stage/install the remote Mac build with a dedicated data directory and an ad-hoc signature."""
import argparse
from datetime import datetime
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install into /Applications after the app is closed")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    source = repo / "out/osx-arm64/Stability Matrix.app"
    target = Path("/Applications/Stability Matrix Remote.app")
    if not source.is_dir():
        parser.error("Build the macOS app first using Build/build_macos_app.sh")
    if args.install:
        processes = subprocess.check_output(["ps", "-axo", "args="], text=True)
        if any(line.startswith(str(target) + "/Contents/MacOS/") for line in processes.splitlines()):
            parser.error("Quit Stability Matrix Remote first; the running app has not been changed")
    stage_root = Path(tempfile.mkdtemp(prefix="remote-app-", dir=repo / "out/osx-arm64"))
    stage = stage_root / "Stability Matrix Remote.app"
    subprocess.run(["ditto", str(source), str(stage)], check=True)
    info_path = stage / "Contents/Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    launcher = stage / "Contents/MacOS/StabilityMatrixRemote"
    launcher.write_text('#!/bin/sh\nexec "$(dirname "$0")/StabilityMatrix.Avalonia" --data-dir "$HOME/Documents/StabilityMatrix-Remote-Data" "$@"\n')
    launcher.chmod(0o755)
    info.update(CFBundleExecutable="StabilityMatrixRemote", CFBundleName="Stability Matrix Remote",
                CFBundleDisplayName="Stability Matrix Remote", CFBundleIdentifier="com.nreck.stabilitymatrix.remote")
    info_path.write_bytes(plistlib.dumps(info))
    subprocess.run(["/bin/sh", "-n", str(launcher)], check=True)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(stage)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(stage)], check=True)
    if args.install:
        if target.exists():
            backup = Path.home() / "Library/Application Support/StabilityMatrixRemote/Backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
            backup.mkdir(parents=True)
            shutil.move(str(target), str(backup / target.name))
        shutil.move(str(stage), str(target))
        stage_root.rmdir()
        print("Installed:", target)
    else:
        print("Staged:", stage)


if __name__ == "__main__":
    main()
