# FreeTDS binaries for Debian trixie

Build [FreeTDS](https://github.com/FreeTDS/freetds) once per stable upstream Git tag and reuse the archive in development and production images, instead of compiling FreeTDS in every application build.

The initial target is **Debian 13 (trixie), Linux x86-64 (`amd64`)**. Builds use `docker run` with `debian:trixie-slim`. The installation prefix is `/usr/local`, with **TDS 7.3**, OpenSSL support, and readline disabled. The archive includes command-line tools such as `tsql`, shared and static libraries, headers, linker symlinks, configuration, documentation, and upstream licenses. It supports building native gems such as TinyTDS. The runtime dependency is Debian's `libssl3t64` in addition to libc; install `ca-certificates` for TLS certificate validation.

## Release automation

[The workflow](.github/workflows/releases.yml) polls the Git tags in `FreeTDS/freetds` once a week, on Mondays at 06:23 UTC. It does not require upstream GitHub release entries. Polling needs no upstream changes or additional credentials. Scheduled runs can be delayed; see [GitHub's trigger documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

On its first run, the workflow builds the **highest stable version tag**, using numeric version ordering (`v1.5.19` is newer than `v1.5.9`). Stable tags have the form `vMAJOR.MINOR` or `vMAJOR.MINOR.PATCH`; prerelease and unrelated tags are ignored. All tag pages are inspected, so discovery does not depend on API ordering.

Subsequent runs build missing stable tags at or above the initial version, including newer versions missed between weekly checks. Complete releases in this repository are skipped; drafts and incomplete uploads are retried. The initial monitoring boundary is preserved when manually backfilling an older tag and when upgrading from the previous release-based workflow. To rebuild an existing tag or backfill an older one, run the workflow manually with `upstream_tag`, for example `v1.5.19`.

For each selected tag, the workflow:

1. Checks out the upstream Git tag.
2. Installs Autotools, gettext, gperf, the C compiler, and OpenSSL development files in a fresh Debian trixie container. Bootstraps the checked-out source with `autogen.sh`, then runs `configure`, `make`, and `make DESTDIR=... install`.
3. Packages the installation, matching upstream source, and SHA-256 checksums.
4. Installs the archive in `ghcr.io/hicknhack-software/ruby-dev:3.4.11`, checks its version, TLS configuration, and shared libraries, then compiles and loads TinyTDS against it. Also tests runtime loading in `ruby:3.4.11-slim-trixie`.
5. Creates a release here with the **upstream tag as both title and tag**, then uploads the files. A separate publishing job has `contents: write`; new releases stay in draft until uploads succeed.

Put these files on this repository's **default branch**, enable Actions, and allow the publishing job to use `contents: write`. Start the initial run using **Actions → Build upstream FreeTDS tags → Run workflow**. The workflow uses the built-in `GITHUB_TOKEN`; no personal token or extra secrets are needed. GitHub may disable scheduled workflows in inactive public repositories after 60 days, so check that the schedule remains enabled.

The workflow uses the upstream Tags API for discovery and the Git reference API to validate manual builds and publication. The Releases API is used only for this repository's binary releases. No upstream release asset or freetds.org download is required. Adjust `FREETDS_REPOSITORY` in the examples if this project's GitHub location differs. Download URLs become available after this repository publishes the corresponding tag.

## Install manually

On Debian trixie amd64:

```sh
FREETDS_REPOSITORY=hicknhack-software/freetds-bin
FREETDS_TAG=v1.5.19
ARCHIVE="freetds-${FREETDS_TAG}-debian-trixie-amd64.tar.gz"
URL="https://github.com/${FREETDS_REPOSITORY}/releases/download/${FREETDS_TAG}"

sudo apt-get update
sudo apt-get install --no-install-recommends -y ca-certificates curl libssl3t64

test "$(dpkg --print-architecture)" = amd64
mkdir -p /tmp/freetds-install
cd /tmp/freetds-install
curl --fail --show-error --location --remote-name "$URL/$ARCHIVE"
curl --fail --show-error --location --remote-name "$URL/$ARCHIVE.sha256"
sha256sum -c "$ARCHIVE.sha256"
sudo tar -xzf "$ARCHIVE" -C /usr/local
sudo ldconfig
tsql -C
```

Extract the **whole archive** into `/usr/local`, preserving symlinks. Configuration lives in `/usr/local/etc/freetds.conf`; back up any existing configuration before extraction because it will be overwritten. These binaries target trixie amd64, not Alpine or older Debian versions.

Native gems still need a compiler and Ruby development headers. The Ruby development image already provides these. For example, force a TinyTDS source build so it uses this installation rather than its platform gem's bundled FreeTDS:

```sh
gem install tiny_tds --platform ruby --no-document -- \
  --with-freetds-dir=/usr/local
ruby -r tiny_tds -e 'puts TinyTds::VERSION'
```

For Bundler, use `bundle config set force_ruby_platform true` and `bundle config set build.tiny_tds '--with-freetds-dir=/usr/local'` before `bundle install`. Forcing Ruby-platform gems affects all gems in the bundle; omit it if the lockfile already selects TinyTDS's Ruby source gem. See [TinyTDS installation instructions](https://github.com/rails-sqlserver/tiny_tds#install).

## Ruby development Dockerfile

Replace the FreeTDS compilation stage with this archive installation. The base's compiler and headers remain available for native gems.

```dockerfile
# syntax=docker/dockerfile:1
ARG RUBY_VERSION=3.4.11
FROM ghcr.io/hicknhack-software/ruby-dev:${RUBY_VERSION}

ARG FREETDS_REPOSITORY=hicknhack-software/freetds-bin
ARG FREETDS_TAG=v1.5.19
RUN <<'INSTALL'
  set -eu
  . /etc/os-release
  test "$VERSION_CODENAME" = trixie
  test "$(dpkg --print-architecture)" = amd64
  apt-get update -qq
  apt-get install --no-install-recommends -y ca-certificates curl libssl3t64
  rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

  archive="freetds-${FREETDS_TAG}-debian-trixie-amd64.tar.gz"
  url="https://github.com/${FREETDS_REPOSITORY}/releases/download/${FREETDS_TAG}"
  mkdir /tmp/freetds-install
  cd /tmp/freetds-install
  curl --fail --show-error --silent --location "$url/$archive" -o "$archive"
  curl --fail --show-error --silent --location "$url/$archive.sha256" -o "$archive.sha256"
  sha256sum -c "$archive.sha256"
  tar -xzf "$archive" -C /usr/local
  cd /
  rm -rf /tmp/freetds-install
  ldconfig
  tsql -C
INSTALL
```

Build with `--platform linux/amd64` when using a host of another architecture.

## Production Dockerfile / Kamal

Pin the Debian variant explicitly so development and production share trixie. Download once into a staging directory and copy the installation into both the gem build and final stages. The final stage needs the shared libraries even when gems were compiled in an earlier stage.

```dockerfile
# syntax=docker/dockerfile:1
ARG RUBY_VERSION=3.4.11
FROM docker.io/library/ruby:${RUBY_VERSION}-slim-trixie AS base
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y ca-certificates libssl3t64 && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

FROM base AS freetds
ARG FREETDS_REPOSITORY=hicknhack-software/freetds-bin
ARG FREETDS_TAG=v1.5.19
RUN <<'INSTALL'
  set -eu
  test "$(dpkg --print-architecture)" = amd64
  apt-get update -qq
  apt-get install --no-install-recommends -y curl
  rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
  archive="freetds-${FREETDS_TAG}-debian-trixie-amd64.tar.gz"
  url="https://github.com/${FREETDS_REPOSITORY}/releases/download/${FREETDS_TAG}"
  mkdir /tmp/freetds-install /opt/freetds
  cd /tmp/freetds-install
  curl --fail --show-error --silent --location "$url/$archive" -o "$archive"
  curl --fail --show-error --silent --location "$url/$archive.sha256" -o "$archive.sha256"
  sha256sum -c "$archive.sha256"
  tar -xzf "$archive" -C /opt/freetds
  cd /
  rm -rf /tmp/freetds-install
INSTALL

FROM base AS build
RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y \
      build-essential git libpq-dev libyaml-dev pkg-config && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
COPY --from=freetds /opt/freetds/ /usr/local/
RUN ldconfig
# Keep your application's gem installation and asset build here.
# Before bundle install, configure TinyTDS to use /usr/local as described above.

FROM base
COPY --from=freetds /opt/freetds/ /usr/local/
RUN ldconfig && tsql -C
# Keep your existing COPY --from=build lines for gems, application, and assets,
# plus your application's user, entrypoint, and command.
```

FreeTDS libraries belong in **`/usr/local/lib/`**, including their versioned symlink targets; call `ldconfig` after copying. Copying libraries directly into `/usr/local/` as in the previous production snippet does not put them on the normal library search path. Keeping the whole installation also retains configuration and licenses.

## Build and verify locally

Requirements: Bash, Docker running Linux containers, and network access to GitHub, Debian, GHCR, Docker Hub, and RubyGems.

```sh
bash scripts/build.sh v1.5.19
bash scripts/smoke-test.sh v1.5.19
bash scripts/smoke-test.sh v1.5.19 ruby:3.4.11-slim-trixie runtime
```

Build output in `dist/`:

```text
freetds-v1.5.19-debian-trixie-amd64.tar.gz
freetds-v1.5.19-debian-trixie-amd64.tar.gz.sha256
freetds-v1.5.19-source.tar.gz
freetds-v1.5.19-source.tar.gz.sha256
```

`BUILD_JOBS` defaults to 2. Optionally pass a clean upstream Git checkout at the requested tag as the second argument to `scripts/build.sh`; this is how CI builds its checked-out tag. Each build uses a disposable container. There is no separate source-selection setting; every build uses Git.

The source archive contains the clean tagged Git source plus generated documentation metadata, so it can be rebuilt without the original `.git` directory. The binary archive records the upstream tag, commit, and build commands in `share/doc/freetds-bin/build-info.txt`. Recipients can bootstrap the source archive with `NOCONFIGURE=1 ./autogen.sh`, then use the recorded configure and make commands.

A version check rejects binaries whose reported version does not match the tag. Some historical upstream tags contain inconsistent version metadata (for example, `v1.5.16` reports 1.5.17); those builds fail explicitly instead of publishing a mislabelled binary. The current `v1.5.19` tag passes the version check.

The development smoke test pins TinyTDS 3.4.1, forces a source build, loads its extension, and checks that it links to `/usr/local/lib/libsybdb.so`. The runtime test loads DB-Library and CT-Library through Ruby's Fiddle. These checks require no database; they do not test queries against SQL Server or Sybase.

To check release discovery and publishing safeguards without Docker:

```sh
python3 -m unittest discover -s tests -v
```

Debian dependency versions follow the current trixie repositories, so builds are not guaranteed to be byte-for-byte reproducible. Each release includes FreeTDS's license notices and matching source; system OpenSSL libraries come from Debian and are not bundled in the archive.
