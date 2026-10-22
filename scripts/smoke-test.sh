#!/usr/bin/env bash
set -euo pipefail
tag=${1:?Usage: scripts/smoke-test.sh UPSTREAM_TAG [IMAGE] [development|runtime]}
[[ $tag =~ ^v[0-9][A-Za-z0-9._-]*$ ]] || { echo 'Invalid upstream tag' >&2; exit 1; }
image=${2:-ghcr.io/hicknhack-software/ruby-dev:3.4.11}
mode=${3:-development}
[[ $mode == development || $mode == runtime ]]
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
docker run --rm --platform linux/amd64 --user root \
  --mount "type=bind,src=$root/dist,dst=/artifacts,readonly" \
  --mount "type=bind,src=$root/scripts,dst=/scripts,readonly" \
  --env "ARCHIVE=freetds-${tag}-debian-trixie-amd64.tar.gz" \
  --env "EXPECTED_VERSION=${tag#v}" --env "TEST_MODE=$mode" \
  --entrypoint bash "$image" /scripts/smoke-test-container.sh
