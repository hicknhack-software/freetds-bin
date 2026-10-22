#!/usr/bin/env python3
"""Download the official FreeTDS release source and verify GitHub's asset digest."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request


def download(tag, destination):
    if not re.fullmatch(r"v[0-9][A-Za-z0-9._-]*", tag):
        raise ValueError("Invalid upstream tag")
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        "https://api.github.com/repos/FreeTDS/freetds/releases/tags/"
        + urllib.parse.quote(tag, safe=""), headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    if release["draft"]:
        raise ValueError("Cannot build an unpublished upstream draft")
    names = [f"freetds-{tag[1:]}.tar.bz2", f"freetds-{tag[1:]}.tar.gz"]
    asset = next((asset for name in names for asset in release["assets"]
                  if asset["name"] == name), None)
    if asset is None:
        raise ValueError(f"No official source archive found for {tag}")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "upstream.tar"
    digest = hashlib.sha256()
    # Do not send the API token to the download/CDN host.
    with urllib.request.urlopen(asset["browser_download_url"], timeout=120) as response, \
            archive.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    actual = "sha256:" + digest.hexdigest()
    if asset.get("digest") and asset["digest"] != actual:
        archive.unlink()
        raise ValueError("Upstream release archive checksum mismatch")
    (destination / "source-info.txt").write_text(
        f"Release source: {asset['browser_download_url']}\n"
        f"Release source digest: {actual}\n"
        f"Verified against GitHub asset digest: {bool(asset.get('digest'))}\n")
    print(f"Downloaded {asset['name']} ({actual})")


if __name__ == "__main__":
    download(*sys.argv[1:])
