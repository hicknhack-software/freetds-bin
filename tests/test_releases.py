import hashlib
import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("releases", "scripts/releases.py")
releases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(releases)


def release(day, *, mirrored=False, draft=False, prerelease=False, assets=True):
    tag = f"v1.5.{day}"
    date = f"2026-10-{day:02}T15:00:00Z"
    archive = releases.asset_name(tag)
    return {
        "id": day, "tag_name": tag, "published_at": date, "draft": draft,
        "prerelease": prerelease,
        "assets": [{"name": name} for name in (archive, archive + ".sha256",
                   f"freetds-{tag}-source.tar.gz", f"freetds-{tag}-source.tar.gz.sha256")] if assets else [],
        "body": f"<!-- freetds-bin upstream-published-at: {date} -->" if mirrored else "",
    }


class DiscoveryTest(unittest.TestCase):
    def discover(self, upstream, local, tag=""):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "output"
            with mock.patch.dict(os.environ, {
                "GITHUB_REPOSITORY": "example/freetds-bin", "GITHUB_OUTPUT": str(output),
            }), mock.patch.object(releases, "releases", return_value=iter(local)), \
                    mock.patch.object(releases, "api", return_value=upstream):
                releases.discover(tag)
            return output.read_text()

    def test_first_run_only_builds_latest_stable(self):
        upstream = [release(20, prerelease=True), release(19), release(12)]
        self.assertEqual(releases.pending(upstream, []), [upstream[1]])

    def test_catches_up_in_order_and_ignores_older_history(self):
        upstream = [release(21), release(19), release(12), release(5)]
        local = [release(12, mirrored=True)]
        self.assertEqual(releases.pending(upstream, local), [upstream[1], upstream[0]])

    def test_no_rebuild_for_complete_releases(self):
        self.assertEqual(releases.pending([release(19)], [release(19, mirrored=True)]), [])

    def test_failed_older_release_is_not_lost_when_newer_one_succeeds(self):
        upstream = [release(21), release(19), release(12)]
        local = [release(12, mirrored=True), release(21, mirrored=True)]
        self.assertEqual(releases.pending(upstream, local), [upstream[1]])

    def test_drafts_and_incomplete_uploads_are_retried(self):
        upstream = [release(21), release(19), release(12)]
        local = [release(12, mirrored=True), release(19, mirrored=True, draft=True),
                 release(21, mirrored=True, assets=False)]
        self.assertEqual(releases.pending(upstream, local), [upstream[1], upstream[0]])

    def test_upstream_drafts_and_prereleases_are_skipped(self):
        upstream = [release(21, draft=True), release(20, prerelease=True), release(19)]
        self.assertEqual(releases.pending(upstream, [release(19, mirrored=True)]), [])

    def test_late_published_release_is_found(self):
        upstream = [release(12), release(21), release(19)]
        self.assertEqual(releases.pending(upstream, [release(19, mirrored=True)]), [upstream[1]])

    def test_empty_upstream(self):
        self.assertEqual(releases.pending([], []), [])

    def test_manual_historical_build_does_not_trigger_a_historical_backlog(self):
        backfill = release(5, mirrored=True)
        backfill["id"] = 99
        local = [release(19, mirrored=True), backfill]
        upstream = [release(21), release(19), release(12), release(5)]
        self.assertEqual(releases.pending(upstream, local), [upstream[0]])

    def test_missing_corresponding_source_is_retried(self):
        local = release(19, mirrored=True)
        local["assets"] = local["assets"][:2]
        self.assertEqual(releases.pending([release(19)], [local]), [release(19)])

    def test_first_run_outputs_latest_tag_for_matrix(self):
        self.assertEqual(self.discover(release(19), []),
                         'tags=["v1.5.19"]\nhas_releases=true\n')

    def test_manual_run_rebuilds_complete_release(self):
        self.assertIn('tags=["v1.5.19"]', self.discover(
            release(19), [release(19, mirrored=True)], tag="v1.5.19"))

    def test_manual_run_rejects_draft(self):
        with self.assertRaises(ValueError):
            self.discover(release(19, draft=True), [], tag="v1.5.19")

    def test_rejects_unsafe_tag_before_passing_to_workflow(self):
        upstream = release(19)
        upstream["tag_name"] = "v1.5.19\ninjected=value"
        with self.assertRaises(ValueError):
            self.discover(upstream, [])

    @mock.patch.object(releases, "api")
    def test_reads_every_release_page(self, api):
        api.side_effect = [[release(19)] * 100, [release(20)]]
        self.assertEqual(len(list(releases.releases("example/freetds-bin"))), 101)
        self.assertEqual(api.call_args_list[-1].args,
                         ("example/freetds-bin/releases?per_page=100&page=2",))


class PublishingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        original = os.getcwd()
        os.chdir(directory.name)
        self.addCleanup(os.chdir, original)
        pathlib.Path("dist").mkdir()
        self.upstream = release(19)
        self.upstream.update(name="FreeTDS 1.5.19 minor release", html_url="https://example.test/release")
        self.tag = self.upstream["tag_name"]
        archive = pathlib.Path("dist") / releases.asset_name(self.tag)
        source = archive.with_name(f"freetds-{self.tag}-source.tar.gz")
        for path in (archive, source):
            path.write_bytes(b"test archive")
            path.with_name(path.name + ".sha256").write_text(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
        self.missing = releases.urllib.error.HTTPError("url", 404, "Not found", {}, None)
        self.addCleanup(self.missing.close)
        environment = mock.patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "example/freetds-bin", "GITHUB_SHA": "123abc",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.real_run = releases.subprocess.run

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_create_draft_upload_then_publish_with_upstream_name(self, api, run):
        api.side_effect = [self.upstream, self.missing]
        releases.publish(self.tag)
        commands = [call.args[0] for call in run.call_args_list if call.args[0][0] == "gh"]
        self.assertEqual([command[2] for command in commands], ["create", "upload", "edit"])
        self.assertIn("--draft", commands[0])
        self.assertIn("FreeTDS 1.5.19 minor release", commands[0])
        self.assertIn("--draft=false", commands[2])

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_existing_release_is_repaired(self, api, run):
        api.side_effect = [self.upstream, release(19, draft=True)]
        releases.publish(self.tag)
        self.assertEqual([call.args[0][2] for call in run.call_args_list if call.args[0][0] == "gh"], ["upload", "edit"])

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_failed_upload_does_not_publish_draft(self, api, run):
        api.side_effect = [self.upstream, self.missing]
        run.side_effect = [None, None, RuntimeError("Upload failed")]
        with self.assertRaises(RuntimeError):
            releases.publish(self.tag)
        self.assertEqual([call.args[0][2] for call in run.call_args_list if call.args[0][0] == "gh"], ["create", "upload"])

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_missing_checksum_prevents_release_creation(self, api, run):
        api.return_value = self.upstream
        next(pathlib.Path("dist").glob("*.sha256")).unlink()
        with self.assertRaises(FileNotFoundError):
            releases.publish(self.tag)
        run.assert_not_called()

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_corrupt_archive_prevents_release_creation(self, api, run):
        api.return_value = self.upstream
        archive = pathlib.Path("dist") / releases.asset_name(self.tag)
        archive.write_bytes(b"corrupted")
        run.side_effect = lambda *args, **kwargs: self.real_run(
            *args, **kwargs, capture_output=True)
        with self.assertRaises(releases.subprocess.CalledProcessError):
            releases.publish(self.tag)
        self.assertEqual(len(run.call_args_list), 1)
        self.assertEqual(run.call_args.args[0][0], "sha256sum")


if __name__ == "__main__":
    unittest.main()
