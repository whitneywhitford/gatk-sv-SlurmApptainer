#!/usr/bin/env python3
# download_gs_and_update_json.py
#
# Find all gs:// paths in a SingleSample inputs JSON, build a local mapping (flat by default),
# optionally download those files with gsutil, and update one or more reference JSONs to
# point to the local copies. Additionally, expand .list files EXCEPT the one referenced in
# GATKSVPipelineSingleSample.ref_samples_list: download every item in the other lists into
# REF/<listname>/ and write a local .list with the local paths; then update JSONs to point
# to that local .list.
#
# Defaults:
#   --singlesample inputs/build/NA12878/test/GATKSVPipelineSingleSample.json
#   --dest        ./REF
#   --update-jsons inputs/value/resources_hg38.json ../../inputs/values/ref_panel_1kg.json
#   Flat layout is DEFAULT: files go to ./REF/<basename>
#
# Examples:
#   python download_gs_and_update_json.py --dry-run --show-diff --verbose
#   python download_gs_and_update_json.py --skip-download --backup
#   python download_gs_and_update_json.py --mirror
#
# Requirements:
#   - Python 3.8+
#   - gsutil (if not using --skip-download and not using --dry-run)

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Set, List, Tuple
import difflib

def parse_args():
    p = argparse.ArgumentParser(
        description="Download gs:// files from SingleSample JSON and update reference JSONs to local paths."
    )
    p.add_argument("--singlesample",
                   default="inputs/build/NA12878/test/GATKSVPipelineSingleSample.json",
                   help="Path to SingleSample inputs JSON to scan for gs:// URIs.")
    p.add_argument("--dest",
                   default="./REF",
                   help="Directory to place downloaded files (created if needed).")
    p.add_argument("--update-jsons", nargs="+",
                   default=["inputs/values/resources_hg38.json",
                            "inputs/values/ref_panel_1kg.json"],
                   help="One or more JSON files to update (strings equal to gs:// URIs will be replaced).")
    p.add_argument("--mirror", action="store_true",
                   help="Mirror bucket structure under dest instead of flat (<dest>/<basename>). Default is flat.")
    p.add_argument("--skip-download", action="store_true",
                   help="Do NOT run gsutil; just compute mappings and update JSONs.")
    p.add_argument("--download-cram", action="store_true",
                   help="Download NA12878.final.cram and its index (default: skipped).")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview actions without downloading or modifying JSONs.")
    p.add_argument("--backup", action="store_true",
                   help="Create a .bak alongside each updated JSON before writing.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Verbose logging.")
    p.add_argument("--show-diff", action="store_true",
                   help="In --dry-run mode, show a unified diff preview for each target JSON.")
    return p.parse_args()

def load_json(path: Path) -> Any:
    with path.open() as f:
        return json.load(f)

def find_gs_uris(obj: Any) -> Set[str]:
    uris: Set[str] = set()
    def rec(x):
        if isinstance(x, dict):
            for v in x.values(): rec(v)
        elif isinstance(x, list):
            for v in x: rec(v)
        elif isinstance(x, str) and x.startswith("gs://"):
            uris.add(x)
    rec(obj)
    return uris

def make_dest_path_for_uri(gs_uri: str, dest_root: Path, mirror: bool) -> Path:
    # gs://bucket/path/to/file -> choose local path
    no_scheme = gs_uri[5:]
    return (dest_root / no_scheme) if mirror else (dest_root / Path(no_scheme).name)

def run_gsutil_cp(src: str, dst: Path, verbose: bool=False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["gsutil"]
    if not verbose: cmd.append("-q")
    cmd += ["cp", src, str(dst)]
    if verbose: print("[gsutil]", " ".join(cmd))
    subprocess.check_call(cmd)

def gsutil_cat(gs_uri: str, verbose: bool=False) -> str:
    cmd = ["gsutil"]
    if not verbose: cmd.append("-q")
    cmd += ["cat", gs_uri]
    if verbose: print("[gsutil]", " ".join(cmd))
    out = subprocess.check_output(cmd, text=True)
    return out

def is_list_uri(uri: str) -> bool:
    return uri.endswith(".list")

def normalize_list_lines(text: str) -> List[str]:
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return lines

def plan_list_downloads(gs_list_uri: str, dest_root: Path, verbose: bool=False):
    # Returns (target_list_path, item_plans) for a .list file.
    # item_plans: list of (src, dst) where dst is a local Path or None for non-gs entries.
    list_basename = Path(gs_list_uri[5:]).name
    list_stem = Path(list_basename).stem
    subdir = dest_root / list_stem
    target_list_path = dest_root / list_basename
    text = gsutil_cat(gs_list_uri, verbose=verbose)
    items = normalize_list_lines(text)
    item_plans = []
    for entry in items:
        if entry.startswith("gs://"):
            dst = subdir / Path(entry[5:]).name
            item_plans.append((entry, dst))
        else:
            item_plans.append((entry, None))
    return target_list_path, item_plans

def write_local_list(target_list_path: Path, item_plans: List[Tuple[str, Path]], dry_run: bool=False, verbose: bool=False):
    lines_out = []
    for src, dst in item_plans:
        if isinstance(src, str) and src.startswith("gs://"):
            lines_out.append(str(dst))
        else:
            lines_out.append(str(src))
    content = "\n".join(lines_out) + "\n"
    if dry_run:
        print(f"[DRY-RUN] Would write list: {target_list_path}")
        if verbose: print(content)
        return
    target_list_path.parent.mkdir(parents=True, exist_ok=True)
    target_list_path.write_text(content)
    if verbose: print(f"Wrote list: {target_list_path} with {len(lines_out)} entries")

def replace_uris_in_obj(obj: Any, mapping: Dict[str, str]) -> Any:
    def rec(x):
        if isinstance(x, dict):
            return {k: rec(v) for k, v in x.items()}
        elif isinstance(x, list):
            return [rec(v) for v in x]
        elif isinstance(x, str) and x in mapping:
            return mapping[x]
        else:
            return x
    return rec(obj)

def unified_diff_preview(original_text: str, updated_text: str, path: Path) -> str:
    diff_lines = difflib.unified_diff(
        original_text.splitlines(keepends=True),
        updated_text.splitlines(keepends=True),
        fromfile=str(path),
        tofile=str(path) + " (updated)",
        n=3
    )
    return "".join(diff_lines)

def gsutil_exists(gs_uri: str, verbose: bool=False) -> bool:
    """
    Return True if a gs:// object exists, else False.
    Uses `gsutil stat` which is cheap and reliable for single-object existence checks.
    """
    cmd = ["gsutil"]
    if not verbose:
        cmd.append("-q")
    cmd += ["stat", gs_uri]
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

def maybe_tbi_for_gz(gs_uri: str) -> str:
    """
    Given gs://...*.gz (but not .gz.tbi), return the corresponding .tbi URI.
    """
    return gs_uri + ".tbi"

def main():
    args = parse_args()

    singlesample_path = Path(args.singlesample).resolve()
    dest_root = Path(args.dest).resolve()
    update_json_paths = [Path(p).resolve() for p in args.update_jsons]

    if not singlesample_path.exists():
        print(f"ERROR: singlesample JSON not found: {singlesample_path}", file=sys.stderr); sys.exit(1)
    for uj in update_json_paths:
        if not uj.exists():
            print(f"ERROR: update JSON not found: {uj}", file=sys.stderr); sys.exit(1)

    if args.verbose:
        print(f"Singlesample: {singlesample_path}")
        print(f"Dest dir:     {dest_root} (mirror={args.mirror}, default=flat)")
        print("Update JSONs:"); [print(f"  - {uj}") for uj in update_json_paths]
        print(f"Modes: dry_run={args.dry_run}, skip_download={args.skip_download}, backup={args.backup}")

    # Load singlesample and extract the .list to exclude (if present)
    singlesample = load_json(singlesample_path)
    exclude_list_uri = singlesample.get("GATKSVPipelineSingleSample.ref_samples_list")

    # Parse singlesample and collect gs:// URIs
    gs_uris = sorted(find_gs_uris(singlesample))
    if args.verbose or args.dry_run:
        print(f"Found {len(gs_uris)} gs:// URIs in singlesample.")
        if exclude_list_uri:
            print(f"Excluding list from expansion: {exclude_list_uri}")

    # Build mapping gs:// -> local path for direct file URIs
    uri_to_local: Dict[str, str] = {}
    for uri in gs_uris:
        dst = make_dest_path_for_uri(uri, dest_root, mirror=args.mirror)
        uri_to_local[uri] = str(dst)

    # --- Expand .list files (skip the one referenced in ref_samples_list) ---
    list_mappings: Dict[str, str] = {}
    for uri in list(gs_uris):
        if is_list_uri(uri) and uri != exclude_list_uri:
            try:
                target_list_path, item_plans = plan_list_downloads(uri, dest_root, verbose=args.verbose)
            except subprocess.CalledProcessError as e:
                print(f"WARNING: failed to read list {uri}: {e}", file=sys.stderr)
                continue

            # Download items inside the list
            for src, dst in item_plans:
                if isinstance(src, str) and src.startswith("gs://") and dst is not None:
                    # Skip CRAM/CRAI unless explicitly requested
                    if (not args.download_cram) and (src.endswith(".cram") or src.endswith(".crai")):
                        if args.verbose:
                            print(f"[SKIP-CRAM] {src} -> {dst}")
                        continue
                    if not args.dry_run and not args.skip_download:
                        run_gsutil_cp(src, dst, verbose=args.verbose)

                        # If this is a bgzipped/tabix-able file and the .tbi exists in GCS, download it too
                        if src.endswith(".gz") and not src.endswith(".gz.tbi"):
                            tbi_uri = maybe_tbi_for_gz(src)
                            if gsutil_exists(tbi_uri, verbose=args.verbose):
                                tbi_dst = Path(str(dst) + ".tbi")
                                run_gsutil_cp(tbi_uri, tbi_dst, verbose=args.verbose)
                            elif args.verbose:
                                print(f"[INFO] No .tbi found for {src}")
                    else:
                        if args.verbose or args.dry_run:
                            print(f"{'[DRY-RUN]' if args.dry_run else '[SKIP-DL]'} {src} -> {dst}")

                            if src.endswith(".gz") and not src.endswith(".gz.tbi"):
                                tbi_uri = maybe_tbi_for_gz(src)
                                print(f"{'[DRY-RUN]' if args.dry_run else '[SKIP-DL]'} (if exists) {tbi_uri} -> {dst}.tbi")

            # Write local .list with replaced paths
            write_local_list(target_list_path, item_plans, dry_run=args.dry_run, verbose=args.verbose)

            # Ensure the list itself is mapped to its local copy
            list_mappings[uri] = str(target_list_path)

    # Merge list mappings so JSONs point to the local list copies
    uri_to_local.update(list_mappings)

    # Download top-level files unless told not to (list items were handled above)
    if args.dry_run or args.skip_download:
        if args.verbose or args.dry_run:
            print("\nPlanned downloads (top-level URIs):")
            for uri, local in uri_to_local.items():
                if is_list_uri(uri):
                    continue
                if (not args.download_cram) and (uri.endswith(".cram") or uri.endswith(".crai")):
                    print(f"  [SKIP-CRAM] {uri} -> {local}")
                else:
                    print(f"  {uri} -> {local}")
                    # If this is a .gz file, also show planned .tbi download
                    if uri.endswith(".gz") and not uri.endswith(".gz.tbi"):
                        print(f"    (if exists) {uri}.tbi -> {local}.tbi")
    else:
        for uri, local in uri_to_local.items():
            if is_list_uri(uri):
                # The list content was handled separately; skip cp for the list itself
                continue
            if (not args.download_cram) and (uri.endswith(".cram") or uri.endswith(".crai")):
                if args.verbose:
                    print(f"[SKIP-CRAM] {uri} -> {local}")
                continue
            run_gsutil_cp(uri, Path(local), verbose=args.verbose)

            # If this is a bgzipped/tabix-able file and the .tbi exists in GCS, download it too
            if uri.endswith(".gz") and not uri.endswith(".gz.tbi"):
                tbi_uri = maybe_tbi_for_gz(uri)
                if gsutil_exists(tbi_uri, verbose=args.verbose):
                    tbi_local = Path(local + ".tbi")
                    run_gsutil_cp(tbi_uri, tbi_local, verbose=args.verbose)
                elif args.verbose:
                    print(f"[INFO] No .tbi found for {uri}")

    # Update target JSONs
    for uj in update_json_paths:
        original_obj = load_json(uj)
        updated_obj = replace_uris_in_obj(original_obj, uri_to_local)
        if args.dry_run:
            if args.show_diff:
                original_text = json.dumps(original_obj, indent=2, ensure_ascii=False) + "\n"
                updated_text = json.dumps(updated_obj, indent=2, ensure_ascii=False) + "\n"
                diff = unified_diff_preview(original_text, updated_text, uj)
                print(f"\n--- Diff preview for {uj} ---")
                print(diff if diff.strip() else "(no changes)")
            else:
                print(f"[DRY-RUN] Would update: {uj}")
        else:
            if args.backup:
                bak = uj.with_suffix(uj.suffix + ".bak")
                shutil.copy2(uj, bak)
                if args.verbose: print(f"Backup created: {bak}")
            with uj.open("w") as f:
                json.dump(updated_obj, f, indent=2)
            if args.verbose: print(f"Updated: {uj}")
    
    try:
        for json_path in args.update_jsons:
            if json_path.endswith("ref_panel_1kg.json"):
                with open(json_path) as f:
                    rp = json.load(f)

                # The key may be called "sample_ids" or similar
                samples = rp.get("sample_ids") or rp.get("samples") or []

                out_list = Path(args.dest) / "ref_panel_1kg.samples.list"
                with open(out_list, "w") as outf:
                    for s in samples:
                        outf.write(f"{s}\n")

                print(f"[INFO] Wrote sample list: {out_list}")
    except Exception as e:
        print(f"[WARN] Could not write ref_panel_1kg sample list: {e}")

    # Final summary
    print("\nSummary")
    print("-------")
    print(f"Singlesample inspected: {singlesample_path}")
    print(f"Destination dir:        {dest_root}")
    print(f"Files referenced:       {len(gs_uris)}")
    if exclude_list_uri:
        print(f"Excluded list:          {exclude_list_uri}")
    if list_mappings:
        print(f"Expanded lists:         {len(list_mappings)} -> placed under REF/<listname>/ and wrote local .list")
    if gs_uris:
        preview = list(uri_to_local.items())[:10]
        print("Example mappings:")
        for src, dst in preview:
            print(f"  {src} -> {dst}")

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"ERROR running external command: {e}", file=sys.stderr)
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        sys.exit(130)
