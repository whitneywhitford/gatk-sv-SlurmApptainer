#!/usr/bin/env bash
set -euo pipefail

# Usage: ./export_docker_images.sh <tags_file> [output_dir]
# <tags_file>: file with one image ref per line (e.g., repo/name:tag)
# [output_dir]: optional, defaults to ../../docker_images

TAGS_FILE="${1:-}"
OUT_DIR="${2:-../../docker_images}"

if [[ -z "$TAGS_FILE" ]]; then
  echo "Usage: $0 <tags_file> [output_dir]"
  exit 1
fi

if [[ ! -f "$TAGS_FILE" ]]; then
  echo "Error: tags file '$TAGS_FILE' not found."
  exit 1
fi

mkdir -p "$OUT_DIR"

while IFS= read -r img; do
  [[ -z "$img" ]] && continue  # skip blank lines
  safe_name=$(echo "$img" | tr '/:' '_')
  out_file="${OUT_DIR}/${safe_name}.tar"
  echo "Saving $img -> $out_file"
  docker save -o "$out_file" "$img"
done < "$TAGS_FILE"

echo "All images exported to $OUT_DIR"
