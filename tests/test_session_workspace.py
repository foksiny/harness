"""
Tests that sessions are stored globally but associated with a workspace folder:
creation tags the session, listing can filter by workspace, and old untracked
sessions remain visible.
"""
import json
import tempfile
import unittest
from pathlib import Path

from harness.core.session import Session, SessionManager


class TestSessionWorkspaceAssociation(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.ws_a = self.base / "project_a"
        self.ws_b = self.base / "project_b"
        self.ws_a.mkdir()
        self.ws_b.mkdir()
        # Global store for these tests.
        self.store = self.base / "global_sessions"
        self.store.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_sessions_are_stored_globally(self):
        sm = SessionManager(storage_dir=self.store, workspace=self.ws_a)
        sa = sm.create("mock", "m", "build", "default", "high", "a session")
        sm_b = SessionManager(storage_dir=self.store, workspace=self.ws_b)
        # Both managers share ONE global directory — sessions are not duplicated
        # per folder.
        self.assertEqual(len(list(self.store.glob("*.json"))), 1)
        # The session carries the workspace it was created in.
        self.assertEqual(sa.workspace, str(self.ws_a.resolve()))
        # And it is visible through a manager bound to another workspace.
        self.assertIsNotNone(sm_b.load(sa.id))

    def test_create_tags_workspace_from_cwd(self):
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(self.ws_a)
            sm = SessionManager(storage_dir=self.store)
            s = sm.create("x", "y", "build", "default", "high", "tag me")
            self.assertEqual(s.workspace, str(self.ws_a.resolve()))
        finally:
            os.chdir(old_cwd)

    def test_list_filters_by_workspace(self):
        sm = SessionManager(storage_dir=self.store, workspace=self.ws_a)
        sa = sm.create("mock", "m", "build", "default", "high", "in a")
        sm.workspace = str(self.ws_b)
        sb = sm.create("mock", "m", "build", "default", "high", "in b")

        all_ids = {s["id"] for s in sm.list_all()}
        self.assertEqual(all_ids, {sa.id, sb.id})

        ids_a = {s["id"] for s in sm.list_all(workspace=str(self.ws_a))}
        ids_b = {s["id"] for s in sm.list_all(workspace=str(self.ws_b))}
        self.assertEqual(ids_a, {sa.id})
        self.assertEqual(ids_b, {sb.id})

        listed_a = sm.list_all(workspace=str(self.ws_a))[0]
        self.assertEqual(listed_a["workspace"], str(self.ws_a.resolve()))

    def test_untracked_legacy_sessions_are_never_hidden(self):
        # A session saved before workspace tracking has no "workspace" field; it
        # must still show up in every workspace listing so nothing is orphaned.
        legacy_id = "sess_20240101_000000_old456"
        self.store.joinpath("sess_20240101_000000_old456.json").write_text(json.dumps({}))
        # include id via json to simulate a stored session
        self.store.joinpath("sess_20240101_000000_old456.json").write_text(json.dumps({
            "id": legacy_id,
            "title": "Legacy",
            "provider": "x",
            "model": "y",
            "mode": "build",
            "permission": "default",
            "thinking_effort": "high",
            "messages": [],
        }))

        sm = SessionManager(storage_dir=self.store, workspace=self.ws_a)
        self.assertIn(legacy_id, {s["id"] for s in sm.list_all()})
        self.assertIn(legacy_id, {s["id"] for s in sm.list_all(workspace=str(self.ws_a))})
        loaded = sm.load(legacy_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.workspace, "")

    def test_old_session_files_without_workspace_load_fine(self):
        data = {
            "id": "sess_20230101_000000_abc111",
            "title": "pre-workspace",
            "provider": "x",
            "model": "y",
            "mode": "build",
            "permission": "default",
            "thinking_effort": "high",
            "messages": [],
        }
        self.store.joinpath("sess_20230101_000000_abc111.json").write_text(json.dumps(data))
        sm = SessionManager(storage_dir=self.store, workspace="")
        s = sm.load("sess_20230101_000000_abc111")
        self.assertIsNotNone(s)
        self.assertIsInstance(s, Session)
        self.assertEqual(s.workspace, "")


if __name__ == "__main__":
    unittest.main()