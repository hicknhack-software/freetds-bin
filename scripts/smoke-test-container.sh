#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == debian && $VERSION_CODENAME == trixie ]]
[[ $(dpkg --print-architecture) == amd64 ]]
ruby -e 'abort RUBY_VERSION unless RUBY_VERSION == "3.4.11"'
apt-get update -qq
apt-get install --no-install-recommends -y ca-certificates libssl3t64
cd /artifacts
sha256sum -c "$ARCHIVE.sha256"
tar -xzf "$ARCHIVE" -C /usr/local
ldconfig
tsql -C | tee /tmp/freetds-version
actual_tag=$(awk '/Version:/ {print $NF}' /tmp/freetds-version)
[[ $actual_tag == "v${EXPECTED_VERSION}" ]]
grep -E 'TDS version: +7\.3$' /tmp/freetds-version
grep -E 'OpenSSL: +yes$' /tmp/freetds-version
for binary in /usr/local/bin/tsql /usr/local/lib/libsybdb.so /usr/local/lib/libct.so; do
  ldd "$binary" | tee /tmp/freetds-ldd
  if grep -q 'not found' /tmp/freetds-ldd; then
    echo "Missing runtime libraries for $binary" >&2
    exit 1
  fi
done
if [[ $TEST_MODE == development ]]; then
  # Force a source build: a platform gem would test its own bundled FreeTDS.
  gem install tiny_tds --version 3.4.1 --platform ruby --no-document -- \
    --with-freetds-dir=/usr/local
  ruby -r tiny_tds -e 'puts "TinyTDS #{TinyTds::VERSION}: native extension loaded"'
  # Ruby expands its own global variable here.
  # shellcheck disable=SC2016
  extension=$(ruby -r tiny_tds -e 'puts $LOADED_FEATURES.find { |f| f.end_with?("tiny_tds.so") }')
  ldd "$extension" | tee /tmp/tiny-tds-ldd
  grep -E 'libsybdb\.so[^ ]* => /usr/local/lib/' /tmp/tiny-tds-ldd
else
  # Runtime verification needs no compiler or development headers.
  ruby -r fiddle -e 'Fiddle.dlopen("libsybdb.so.5"); Fiddle.dlopen("libct.so.4"); puts "FreeTDS libraries loaded"'
fi
rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
