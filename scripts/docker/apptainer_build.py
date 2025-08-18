#!/usr/bin/env python3
import argparse
import json
import shutil
import sys
import subprocess
from pathlib import Path

def load_json(p: Path):
    with p.open("r") as f:
        return json.load(f)

def save_json(p: Path, data):
    with p.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

def build_sifs_from_tars(img_dir: Path, dockers_json_path: Path, dry_run: bool) -> int:
    if not img_dir.exists():
        print(f"Directory {img_dir} does not exist.", file=sys.stderr)
        return 1

    tar_files = sorted(img_dir.glob("*.tar"))
    if not tar_files:
        print(f"No .tar files found in {img_dir}")

    if not dry_run and tar_files and shutil.which("apptainer") is None:
        print("ERROR: 'apptainer' not found in PATH.", file=sys.stderr)
        return 2

    # Index artifacts by base name (stem)
    tars_by_base = {t.stem: t for t in tar_files}
    sifs_by_base = {s.stem: s for s in img_dir.glob("*.sif")}

    # Build missing .sif files from .tar
    built, skipped = [], []
    for base, tarfile in tars_by_base.items():
        siffile = img_dir / f"{base}.sif"
        if dry_run:
            action = "skip (exists)" if siffile.exists() else "build"
            print(f"[DRY-RUN] Would {action}: {siffile.name} from {tarfile.name}")
            continue
        if siffile.exists():
            print(f"Skipping {siffile.name} (already exists)")
            skipped.append(siffile.name)
        else:
            print(f"Building {siffile.name} from {tarfile.name}")
            subprocess.run(["apptainer", "build", str(siffile), f"docker-archive:{tarfile}"], check=True)
            built.append(siffile.name)

    if not dry_run:
        sifs_by_base = {s.stem: s for s in img_dir.glob("*.sif")}

    # Update dockers.json values to the base filename (no .sif)
    # This works if the value matches a .tar/.sif stem or is "name:tag" matching a .tar/.sif stem after ':' ? '_'
    updates = []
    try:
        dockers_data = load_json(dockers_json_path)
    except FileNotFoundError:
        print(f"WARNING: {dockers_json_path} not found; skipping JSON updates.")
        dockers_data = {}

    def candidate_base_from_value(val: str):
        p = Path(val)
        if p.stem in tars_by_base or p.stem in sifs_by_base:
            return p.stem
        # handle name:tag (no registry) by replacing ':' with '_'
        if ":" in val and "/" not in val:
            base = val.replace(":", "_")
            if base in tars_by_base or base in sifs_by_base:
                return base
        return None

    for key, val in list(dockers_data.items()):
        if key == "name":
            continue
        base = candidate_base_from_value(val)
        if base and val != base:
            updates.append((key, val, base))
            if not dry_run:
                dockers_data[key] = base

    # Summary
    print("\nSummary:")
    if dry_run:
        to_build = sum(1 for b in tars_by_base if not (img_dir / f"{b}.sif").exists())
        to_skip  = sum(1 for b in tars_by_base if (img_dir / f"{b}.sif").exists())
        print(f"  Would build:   {to_build}")
        print(f"  Would skip:    {to_skip}")
        if updates:
            print("  Would update dockers.json:")
            for k, old, new in updates:
                print(f"    {k}: {old}  ->  {new}")
        else:
            print("  Would update dockers.json: 0 changes")
        print("[DRY-RUN] No files were changed.")
    else:
        print(f"  Built:   {len(built)}")
        for b in built: print(f"    {b}")
        print(f"  Skipped: {len(skipped)}")
        for s in skipped: print(f"    {s}")
        if updates:
            print("  Updated dockers.json:")
            for k, old, new in updates:
                print(f"    {k}: {old}  ->  {new}")
            save_json(dockers_json_path, dockers_data)
            print(f"  Saved {dockers_json_path}")
        else:
            print("  dockers.json already aligned; no changes saved.")

    return 0

def parse_args():
    p = argparse.ArgumentParser(
        description="Build .sif from all .tar in a directory and update dockers.json values to the basename (no .sif)."
    )
    p.add_argument(
        "dir",
        nargs="?",
        default="docker_images",
        help="Directory containing .tar/.sif (default: docker_images)",
    )
    p.add_argument(
        "--dockers-json",
        default="inputs/values/dockers.json",
        help="Path to dockers.json to update (default: dockers.json)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be built/updated, but make no changes.",
    )
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    sys.exit(build_sifs_from_tars(Path(args.dir), Path(args.dockers_json), args.dry_run))
