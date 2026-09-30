#!/usr/bin/env python3
"""Run on the server to expose a model drive to existing backends without moving files."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models")
    parser.add_argument("--comfy", default="~/ComfyUI")
    parser.add_argument("--library", default="~/Applications/StabilityMatrix/Data")
    args = parser.parse_args()
    root = Path(args.models).expanduser().resolve()
    if not root.is_dir():
        parser.error("Model drive directory must already exist and be accessible")
    library = Path(args.library).expanduser().resolve()
    comfy = Path(args.comfy).expanduser().resolve()
    mapping = {"checkpoints": "StableDiffusion", "loras": "Lora", "vae": "VAE",
               "text_encoders": "TextEncoders", "diffusion_models": "DiffusionModels",
               "controlnet": "ControlNet", "clip_vision": "ClipVision", "upscale_models": "ESRGAN",
               "embeddings": "Embeddings"}
    links = []
    for category, folder in mapping.items():
        target = root / folder
        if (comfy / "main.py").is_file():
            links.append((comfy / "models" / category / "RemoteDownloads", target))
        if (library / "settings.json").is_file():
            links.append((library / "Models" / folder / "RemoteDownloads", target))
    for link, target in links:
        if (link.exists() or link.is_symlink()) and (not link.is_symlink() or link.resolve() != target):
            raise FileExistsError("Existing path left unchanged: " + str(link))
    for link, target in links:
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.is_symlink():
            link.symlink_to(target, target_is_directory=True)
        print(str(link) + " -> " + str(target))


if __name__ == "__main__":
    main()
