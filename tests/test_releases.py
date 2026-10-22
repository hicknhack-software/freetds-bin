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


def release(version, *, mirrored=True, draft=False, assets=True):
    tag = f"v1.5.{version}"
    archive = releases.asset_name(tag)
    return {
        "id": version, "tag_name": tag, "draft": draft,
        "assets": [{"name": name} for name in (archive, archive + ".sha256",
                   f"freetds-{tag}-source.tar.gz", f"freetds-{tag}-source.tar.gz.sha256")] if assets else [],
        "body": f"<!-- freetds-bin upstream-tag: {tag} -->" if mirrored else "",
    }


class DiscoveryTest(unittest.TestCase):
    def discover(self, upstream, local, tag=""):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "output"

            def api(path):
                if path == "example/freetds-bin/releases?per_page=100&page=1":
                    return local
                if path == "FreeTDS/freetds/tags?per_page=100&page=1":
                    return [{"name": name} for name in upstream]
                if path.startswith("FreeTDS/freetds/git/ref/tags/"):
                    requested = path.rsplit("/", 1)[1]
                    if requested in upstream:
                        return {"ref": f"refs/tags/{requested}", "object": {"type": "tag"}}
                    raise releases.urllib.error.HTTPError("url", 404, "Not found", {}, None)
                self.fail(f"Unexpected API request: {path}")

            with mock.patch.dict(os.environ, {
                "GITHUB_REPOSITORY": "example/freetds-bin", "GITHUB_OUTPUT": str(output),
            }), mock.patch.object(releases, "api", side_effect=api):
                releases.discover(tag)
            return output.read_text()

    def test_first_run_only_builds_latest_stable_tag_without_release_entry(self):
        upstream = ["v1.5.20rc1", "v1.5.19", "v1.5.12", "nightly"]
        self.assertEqual(self.discover(upstream, []),
                         'tags=["v1.5.19"]\nhas_tags=true\n')

    def test_numeric_version_order_instead_of_api_or_lexical_order(self):
        self.assertEqual(releases.pending(["v1.5.9", "v1.5.19", "v1.5.2"], []),
                         ["v1.5.19"])
        self.assertEqual(releases.pending(["v1.9", "v1.10", "v1.10.1", "v2.0"], []),
                         ["v2.0"])

    def test_major_and_minor_release_tags_are_supported(self):
        self.assertEqual(releases.pending(["v1.4.99", "v1.5"], []), ["v1.5"])
        self.assertEqual(releases.version_key("v1.5"), (1, 5, 0))

    def test_catches_up_in_order_and_ignores_older_history(self):
        upstream = ["v1.5.21", "v1.5.19", "v1.5.12", "v1.5.5"]
        self.assertEqual(releases.pending(upstream, [release(12)]),
                         ["v1.5.19", "v1.5.21"])

    def test_no_rebuild_for_complete_releases(self):
        self.assertEqual(releases.pending(["v1.5.19"], [release(19)]), [])
        self.assertEqual(self.discover(["v1.5.19"], [release(19)]),
                         'tags=[]\nhas_tags=false\n')

    def test_failed_older_tag_is_not_lost_when_newer_one_succeeds(self):
        self.assertEqual(releases.pending(["v1.5.21", "v1.5.19", "v1.5.12"],
                                         [release(12), release(21)]), ["v1.5.19"])

    def test_drafts_and_incomplete_uploads_are_retried(self):
        local = [release(12), release(19, draft=True), release(21, assets=False)]
        self.assertEqual(releases.pending(["v1.5.21", "v1.5.19", "v1.5.12"], local),
                         ["v1.5.19", "v1.5.21"])

    def test_prerelease_and_unrelated_tags_are_skipped(self):
        self.assertEqual(releases.pending(
            ["v1.5.21-rc1", "v1.5.20rc1", "nightly", "v1.5.19"], [release(19)]), [])

    def test_empty_upstream(self):
        self.assertEqual(self.discover([], []), 'tags=[]\nhas_tags=false\n')

    def test_manual_historical_build_does_not_trigger_a_historical_backlog(self):
        backfill = release(5)
        backfill["id"] = 99
        self.assertEqual(releases.pending(["v1.5.21", "v1.5.19", "v1.5.12", "v1.5.5"],
                                         [release(19), backfill]), ["v1.5.21"])

    def test_migrates_monitoring_boundary_from_previous_release_based_notes(self):
        local = release(16)
        local["body"] = "<!-- freetds-bin upstream-published-at: 2026-03-29T21:04:19Z -->"
        self.assertEqual(releases.pending(["v1.5.15", "v1.5.16", "v1.5.17", "v1.5.19"],
                                         [local]), ["v1.5.17", "v1.5.19"])

    def test_missing_corresponding_source_is_retried(self):
        local = release(19)
        local["assets"] = local["assets"][:2]
        self.assertEqual(releases.pending(["v1.5.19"], [local]), ["v1.5.19"])

    def test_manual_run_rebuilds_complete_release(self):
        self.assertIn('tags=["v1.5.19"]', self.discover(
            ["v1.5.19"], [release(19)], tag="v1.5.19"))

    def test_manual_run_rejects_missing_tag(self):
        with self.assertRaises(releases.urllib.error.HTTPError) as caught:
            self.discover([], [], tag="v1.5.99")
        caught.exception.close()

    def test_manual_run_rejects_unsafe_or_prerelease_tag(self):
        for tag in ("v1.5.19\ninjected=value", "v1.5.20rc1", "--invalid"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.discover([], [], tag=tag)

    @mock.patch.object(releases, "api")
    def test_reads_every_upstream_tag_page(self, api):
        api.side_effect = [[{"name": "v1.5.19"}] * 100, [{"name": "v1.5.20"}]]
        self.assertEqual(len(list(releases.pages("FreeTDS/freetds", "tags"))), 101)
        self.assertEqual(api.call_args_list[-1].args,
                         ("FreeTDS/freetds/tags?per_page=100&page=2",))

    @mock.patch.object(releases, "api")
    def test_reads_every_local_release_page(self, api):
        api.side_effect = [[release(19)] * 100, [release(20)]]
        self.assertEqual(len(list(releases.pages("example/freetds-bin", "releases"))), 101)
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
        self.tag = "v1.5.19"
        self.upstream = {"ref": f"refs/tags/{self.tag}", "object": {"type": "tag"}}
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
    def test_create_draft_upload_then_publish_without_upstream_release(self, api, run):
        api.side_effect = [self.upstream, self.missing]
        notes = []

        def capture_notes(command, **kwargs):
            if "--notes-file" in command:
                notes.append(pathlib.Path(command[command.index("--notes-file") + 1]).read_text())

        run.side_effect = capture_notes
        releases.publish(self.tag)
        commands = [call.args[0] for call in run.call_args_list if call.args[0][0] == "gh"]
        self.assertEqual([command[2] for command in commands], ["create", "upload", "edit"])
        self.assertIn("--draft", commands[0])
        self.assertEqual(commands[0][commands[0].index("--title") + 1], self.tag)
        self.assertIn("--draft=false", commands[2])
        self.assertIn("--prerelease=false", commands[2])
        self.assertIn("https://github.com/FreeTDS/freetds/tree/v1.5.19", notes[0])
        self.assertIn("<!-- freetds-bin upstream-tag: v1.5.19 -->", notes[0])
        self.assertEqual(api.call_args_list[0].args,
                         ("FreeTDS/freetds/git/ref/tags/v1.5.19",))
        self.assertTrue(all(not call.args[0].startswith("FreeTDS/freetds/releases")
                            for call in api.call_args_list))

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_existing_release_is_repaired(self, api, run):
        api.side_effect = [self.upstream, release(19, draft=True)]
        releases.publish(self.tag)
        self.assertEqual([call.args[0][2] for call in run.call_args_list if call.args[0][0] == "gh"],
                         ["upload", "edit"])

    @mock.patch.object(releases.subprocess, "run")
    @mock.patch.object(releases, "api")
    def test_failed_upload_does_not_publish_draft(self, api, run):
        api.side_effect = [self.upstream, self.missing]
        run.side_effect = [None, None, RuntimeError("Upload failed")]
        with self.assertRaises(RuntimeError):
            releases.publish(self.tag)
        self.assertEqual([call.args[0][2] for call in run.call_args_list if call.args[0][0] == "gh"],
                         ["create", "upload"])

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
