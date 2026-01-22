#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------- robust brace-matching helpers ----------

def _find_brace_block(text, start_idx):
    """Return (block_string, end_idx) for the balanced {...} starting at start_idx."""
    if text[start_idx] != "{":
        raise ValueError("Expected '{' at start_idx")
    depth = 0
    in_squote = False
    in_dquote = False
    escape = False
    for i in range(start_idx, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and (in_squote or in_dquote):
            escape = True
            continue
        if ch == "'" and not in_dquote:
            in_squote = not in_squote
            continue
        if ch == '"' and not in_squote:
            in_dquote = not in_dquote
            continue
        if in_squote or in_dquote:
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx + 1:i], i
    raise ValueError("Unbalanced braces while parsing")

def _extract_dependencies_block(text):
    m = re.search(r"\bdependencies\s*=\s*{", text)
    if not m:
        raise ValueError("Could not find 'dependencies = {' in build_docker.py")
    open_idx = m.end() - 1
    block, _ = _find_brace_block(text, open_idx)
    return block

# ---------- parse buildable image names from build_docker.py ----------

def extract_all_buildable_images(build_docker_path: Path):
    """
    Return set of ALL image names the script can build:
    - keys in ProjectBuilder.dependencies
    - any image names appearing inside docker_dependencies={...}
    """
    text = build_docker_path.read_text()
    deps_block = _extract_dependencies_block(text)

    # Top-level keys
    buildable = set(re.findall(r'["\']([^"\']+)["\']\s*:\s*ImageDependencies\s*\(', deps_block))

    # Keys inside docker_dependencies maps
    search_pos = 0
    while True:
        m = re.search(r"\bdocker_dependencies\s*=\s*{", deps_block[search_pos:])
        if not m:
            break
        start = search_pos + m.end() - 1
        block, end_idx = _find_brace_block(deps_block, start)
        buildable.update(re.findall(r'["\']([^"\']+)["\']\s*:', block))
        search_pos = start + (end_idx - start) + 1

    return buildable

# ---------- utils ----------

def normalize_key(json_key: str) -> str:
    """
    Make dockers.json keys comparable to build_docker target names.
    Strip suffixes and replace '_' with '-'.
    """
    key = json_key
    for suffix in ("_docker", "_virtual_env", "_env"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    return key.replace("_", "-")

def docker_url_to_basename(url: str) -> str:
    """repo/name:tag -> name_tag"""
    if url.startswith("docker://"):
        url = url[len("docker://"):]
    last = url.split("/")[-1]
    return last.replace(":", "_")

def load_json(p: Path):
    with p.open("r") as f:
        return json.load(f)

def save_json(p: Path, data):
    with p.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

# ---------- core operations ----------

def compute_unbuilt_keys(build_docker_path: Path, dockers_json: dict) -> list[str]:
    """Figure out which dockers.json keys are NOT buildable by build_docker.py (including dep equivalences by URL)."""
    buildable_names = extract_all_buildable_images(build_docker_path)

    json_keys = set(dockers_json.keys()) - {"name"}
    # A) buildable by normalized name
    buildable_keys = {k for k in json_keys if normalize_key(k) in buildable_names}

    # B) keys that share the same URL as a buildable key are also considered buildable
    buildable_urls = {dockers_json[k] for k in buildable_keys if isinstance(dockers_json[k], str)}
    for k in json_keys:
        if isinstance(dockers_json[k], str) and dockers_json[k] in buildable_urls:
            buildable_keys.add(k)

    # Final unbuilt
    return sorted(json_keys - buildable_keys)

def pull_sifs_for_keys(unbuilt_keys: list[str], dockers_json: dict, out_dir: Path, dry_run: bool) -> dict[str, str]:
    """
    Pull each unbuilt URL with apptainer into out_dir, return mapping key->basename (no .sif).
    Deduplicate by URL.
    """
    out_dir.mkdir(parents=True, exist_ok=True) if not dry_run else None
    url_to_basename: dict[str, str] = {}
    updates: dict[str, str] = {}

    for key in unbuilt_keys:
        url = dockers_json[key]
        if not isinstance(url, str):
            continue
        # Apptainer requires lowercase image references (Docker allows uppercase in tags)
        url_no_scheme = url[len("docker://"):] if url.startswith("docker://") else url
        pull_url = "docker://" + url_no_scheme.lower()

        base = docker_url_to_basename(url)  # keep original for stable filenames/updates
        sif_path = out_dir / f"{base}.sif"

        if dry_run:
            print(f"[DRY-RUN] Would pull {pull_url} -> {sif_path}")
        else:
            # Deduplicate pulls case-insensitively (Apptainer lowercases refs anyway)
            dedupe_key = url_no_scheme.lower()

            if dedupe_key not in url_to_basename:
                if sif_path.exists():
                    print(f"Skipping pull for {sif_path} (already exists)")
                else:
                    print(f"Pulling {pull_url} -> {sif_path}")
                    subprocess.run(["apptainer", "pull", str(sif_path), pull_url], check=True)
                url_to_basename[dedupe_key] = base
            base = url_to_basename[dedupe_key]

        updates[key] = base  # update dockers.json to basename (no .sif)
    return updates

def apply_updates(dockers_json_path: Path, dockers_json: dict, updates: dict[str, str], dry_run: bool):
    if not updates:
        print("No updates to dockers.json needed.")
        return
    print("\nPlanned updates to dockers.json:")
    for k, new in updates.items():
        old = dockers_json.get(k)
        print(f"  {k}: {old}  ->  {new}")
        if not dry_run:
            dockers_json[k] = new
    if not dry_run:
        save_json(dockers_json_path, dockers_json)
        print(f"Saved {dockers_json_path}")
    else:
        print("[DRY-RUN] Not writing changes.")

# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(
        description="Pull unbuildable Docker images (per build_docker.py) into Apptainer .sif and update dockers.json."
    )
    ap.add_argument(
        "--build-docker",
        default="scripts/docker/build_docker.py",
        help="Path to build_docker.py (default: scripts/docker/build_docker.py)",
    )
    ap.add_argument(
        "--dockers-json",
        default="inputs/values/dockers.json",
        help="Path to dockers.json (default: inputs/values/dockers.json)",
    )
    ap.add_argument(
        "--out-dir",
        default="docker_images/",
        help="Directory to store .sif files (default: docker_images/)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be pulled/updated, but make no changes.",
    )
    args = ap.parse_args()

    build_docker_path = Path(args.build_docker)
    dockers_json_path = Path(args.dockers_json)
    out_dir = Path(args.out_dir)

    print(f"Using build_docker.py: {build_docker_path}")
    print(f"Using dockers.json:    {dockers_json_path}")
    print(f"Output .sif dir:       {out_dir}")
    print(f"Dry run:               {args.dry_run}")

    dockers = load_json(dockers_json_path)

    # 1) decide which keys are not buildable by build_docker.py
    unbuilt_keys = compute_unbuilt_keys(build_docker_path, dockers)
    print("\nImages not built by build_docker.py (will be pulled):")
    if not unbuilt_keys:
        print("  (none)")
    else:
        for k in unbuilt_keys:
            print(f"  {k} -> {dockers[k]}")

    # 2) pull and compute updates (key -> basename)
    updates = pull_sifs_for_keys(unbuilt_keys, dockers, out_dir, dry_run=args.dry_run)

    # 3) write updates back to dockers.json (values set to basename without .sif)
    apply_updates(dockers_json_path, dockers, updates, dry_run=args.dry_run)

if __name__ == "__main__":
    sys.exit(main())
