#!/usr/bin/env python3
"""Discover upstream releases and publish verified archives using GITHUB_TOKEN."""
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
MARKER = re.compile(r"<!-- freetds-bin upstream-published-at: ([^ ]+) -->")


def api(path):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{path}",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def releases(repository):
    page = 1
    while True:
        batch = api(f"{repository}/releases?per_page=100&page={page}")
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


def baseline(local):
    marked = [(item["id"], match[1]) for item in local
              if (match := MARKER.search(item.get("body") or ""))]
    # Release IDs increase over time. A manual backfill must not move the
    # initial monitoring boundary back and trigger years of historical builds.
    return min(marked)[1] if marked else None


def pending(upstream, local):
    """Bootstrap at latest; subsequently include every release since that baseline."""
    stable = [item for item in upstream if not item["draft"] and not item["prerelease"]]
    if not stable:
        return []
    since = baseline(local) or max(item["published_at"] for item in stable)
    done = {item["tag_name"] for item in local if complete(item)}
    return sorted((item for item in stable
                   if item["published_at"] >= since and item["tag_name"] not in done),
                  key=lambda item: item["published_at"])


def discover(tag):
    local = list(releases(os.environ["GITHUB_REPOSITORY"]))
    if tag:
        chosen = [api(f"{UPSTREAM}/releases/tags/{urllib.parse.quote(tag, safe='')}")]
    else:
        if baseline(local):
            # Releases are returned by creation time, not necessarily publication
            # time. Read all pages so a late publication cannot be overlooked.
            chosen = pending(list(releases(UPSTREAM)), local)
        else:
            chosen = pending([api(f"{UPSTREAM}/releases/latest")], local)
    for item in chosen:
        if item["draft"]:
            raise ValueError("Cannot build an unpublished upstream draft")
        if not re.fullmatch(r"v[0-9][A-Za-z0-9._-]*", item["tag_name"]):
            raise ValueError(f"Unsupported upstream tag: {item['tag_name']}")
    tags = [item["tag_name"] for item in chosen]
    # A matrix supports at most 256 jobs. The remaining backlog is picked up later.
    tags = tags[:256]
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"tags={json.dumps(tags)}\n")
        output.write(f"has_releases={'true' if tags else 'false'}\n")
    print(f"Releases to build: {tags}")


def publish(tag):
    repository = os.environ["GITHUB_REPOSITORY"]
    upstream = api(f"{UPSTREAM}/releases/tags/{urllib.parse.quote(tag, safe='')}")
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
        f"Upstream release: {upstream['html_url']}\n\n"
        "Install the archive into `/usr/local`; see readme.md for dependencies "
        "and Dockerfile instructions. Includes headers and linker files for native gems. "
        "Built with TDS 7.3 and OpenSSL. Matching source and licenses are included.\n\n"
        "Verified in `ghcr.io/hicknhack-software/ruby-dev:3.4.11`.\n\n"
        f"<!-- freetds-bin upstream-published-at: {upstream['published_at']} -->\n"
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
               "--title", upstream["name"] or tag, "--notes-file", notes.name)
        gh("upload", tag, *(str(path) for path in artifacts), "--clobber")
        gh("edit", tag, "--draft=false", "--title", upstream["name"] or tag,
           "--notes-file", notes.name,
           f"--prerelease={'true' if upstream['prerelease'] else 'false'}")


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
