#!/usr/bin/env bash
set -euo pipefail
tag=${1:?Usage: scripts/build.sh UPSTREAM_TAG [UPSTREAM_CHECKOUT]}
[[ $tag =~ ^v[0-9][A-Za-z0-9._-]*$ ]] || { echo 'Invalid upstream tag' >&2; exit 1; }
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p "$root/dist"
release_args=()
if [[ ${FREETDS_SOURCE:-release} == release ]]; then
  release_dir="$root/.build/source-$tag"
  python3 "$root/scripts/source.py" "$tag" "$release_dir"
  release_args=(--mount "type=bind,src=$release_dir,dst=/release,readonly")
elif [[ $FREETDS_SOURCE != git ]]; then
  echo 'FREETDS_SOURCE must be release or git' >&2
  exit 1
fi
source_args=()
if [[ -n ${2:-} ]]; then
  source_args=(--mount "type=bind,src=$(cd "$2" && pwd),dst=/source,readonly")
fi
docker run --rm --platform linux/amd64 \
  --mount "type=bind,src=$root,dst=/workspace,readonly" \
  --mount "type=bind,src=$root/dist,dst=/output" \
  "${source_args[@]}" \
  "${release_args[@]}" \
  --env "FREETDS_TAG=$tag" --env "BUILD_JOBS=${BUILD_JOBS:-2}" \
  debian:trixie-slim bash /workspace/scripts/build-container.sh
