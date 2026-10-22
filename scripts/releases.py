#!/usr/bin/env python3
"""Discover upstream Git tags and publish verified archives using GITHUB_TOKEN."""
import argparse
import json
import os
import pathlib
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

UPSTREAM = "FreeTDS/freetds"
MARKER = re.compile(r"<!-- freetds-bin upstream-tag: (v[0-9.]+) -->")
LEGACY_MARKER = re.compile(r"<!-- freetds-bin upstream-published-at: [^ ]+ -->")
STABLE_TAG = re.compile(r"v([0-9]+)\.([0-9]+)(?:\.([0-9]+))?")


def api(path):
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{path}",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def pages(repository, endpoint):
    page = 1
    while True:
        batch = api(f"{repository}/{endpoint}?per_page=100&page={page}")
        yield from batch
        if len(batch) < 100:
            break
        page += 1


def asset_name(tag):
    return f"freetds-{tag}-debian-trixie-amd64.tar.gz"


def complete(release):
    asset = asset_name(release["tag_name"])
    source = f"freetds-{release['tag_name']}-source.tar.gz"
    names = {item["name"] for item in release.get("assets", [])}
    return not release["draft"] and {asset, f"{asset}.sha256", source, f"{source}.sha256"} <= names


def version_key(tag):
    match = STABLE_TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"Unsupported stable upstream tag: {tag}")
    return tuple(int(part or 0) for part in match.groups())


def baseline(local):
    marked = [(item["id"], version_key(item["tag_name"])) for item in local
              if STABLE_TAG.fullmatch(item["tag_name"])
              and (MARKER.search(item.get("body") or "")
                   or LEGACY_MARKER.search(item.get("body") or ""))]
    # Keep the original monitoring boundary when manually backfilling an old tag.
    # Recognize notes from the earlier GitHub-release-based implementation too.
    return min(marked)[1] if marked else None


def pending(upstream, local):
    """Bootstrap at the highest stable version, then catch up from that version."""
    stable = {tag for tag in upstream if STABLE_TAG.fullmatch(tag)}
    if not stable:
        return []
    since = baseline(local) or max(version_key(tag) for tag in stable)
    done = {item["tag_name"] for item in local if complete(item)}
    return sorted((tag for tag in stable
                   if version_key(tag) >= since and tag not in done), key=version_key)


def upstream_tag(tag):
    version_key(tag)
    # Works for both annotated and lightweight tags, without a release entry.
    return api(f"{UPSTREAM}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")


def discover(tag):
    local = list(pages(os.environ["GITHUB_REPOSITORY"], "releases"))
    if tag:
        upstream_tag(tag)
        chosen = [tag]
    else:
        # Read all pages; API ordering is not a numeric version ordering.
        chosen = pending([item["name"] for item in pages(UPSTREAM, "tags")], local)
    tags = chosen[:256]
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"tags={json.dumps(tags)}\n")
        output.write(f"has_tags={'true' if tags else 'false'}\n")
    print(f"Tags to build: {tags}")


def publish(tag):
    repository = os.environ["GITHUB_REPOSITORY"]
    upstream_tag(tag)
    archive = pathlib.Path("dist") / asset_name(tag)
    checksum = archive.with_name(archive.name + ".sha256")
    source = archive.with_name(f"freetds-{tag}-source.tar.gz")
    artifacts = [archive, checksum, source, source.with_name(source.name + ".sha256")]
    if not all(path.is_file() for path in artifacts):
        raise FileNotFoundError("Binary, source, and both checksums must exist")
    subprocess.run(["sha256sum", "--check", checksum.name, source.name + ".sha256"],
                   cwd=archive.parent, check=True)
    body = (
        f"Prebuilt FreeTDS for **Debian trixie, linux/amd64**.\n\n"
        f"Upstream tag: https://github.com/{UPSTREAM}/tree/{tag}\n\n"
        "Install the archive into `/usr/local`; see readme.md for dependencies "
        "and Dockerfile instructions. Includes headers and linker files for native gems. "
        "Built with TDS 7.3 and OpenSSL. Matching source and licenses are included.\n\n"
        "Verified in `ghcr.io/hicknhack-software/ruby-dev:3.4.11`.\n\n"
        f"<!-- freetds-bin upstream-tag: {tag} -->\n"
    )

    def gh(*args):
        subprocess.run(["gh", "release", *args, "--repo", repository], check=True)

    try:
        existing = api(f"{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        existing = None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as notes:
        notes.write(body)
        notes.flush()
        if existing is None:
            gh("create", tag, "--target", os.environ["GITHUB_SHA"], "--draft",
               "--title", tag, "--notes-file", notes.name)
        gh("upload", tag, *(str(path) for path in artifacts), "--clobber")
        gh("edit", tag, "--draft=false", "--title", tag,
           "--notes-file", notes.name,
           "--prerelease=false")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["discover", "publish"])
    parser.add_argument("--tag", default="")
    args = parser.parse_args()
    if args.command == "discover":
        discover(args.tag)
    else:
        if not args.tag:
            parser.error("publish requires --tag")
        publish(args.tag)
