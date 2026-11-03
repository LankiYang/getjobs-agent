"""Regression tests for user-scoped task and event records."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, work_items


class WorkItemsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = patch.object(db, "CONTROL_DB", Path(self.directory.name) / "control.db")
        self.db_path.start()
        self.addCleanup(self.db_path.stop)
        db.init_db()

    def call(self, user, kind, action, data):
        return work_items.execute(user, kind, action, data)

    def test_crud_partial_update_and_explicit_deletion(self):
        created = self.call(1, "task", "create", {"fields": {
            "name": "申请批次", "status": "planned", "nested": {"a": 1}, "nullable": None,
        }})
        self.assertTrue(created["ok"])
        item = created["item"]
        item_id = item["id"]
        self.assertEqual(item["kind"], "task")
        updated = self.call(1, "task", "update", {"id": item_id,
                            "fields": {"nested": {"b": 2}, "date": "2026-12-01"},
                            "delete_fields": ["status", "missing"]})
        self.assertEqual(updated["item"]["fields"], {"name": "申请批次", "nested": {"b": 2},
                                                     "nullable": None, "date": "2026-12-01"})
        self.assertEqual(self.call(1, "task", "get", {"id": item_id})["item"], updated["item"])
        self.assertEqual(self.call(1, "task", "delete", {"id": item_id})["item"], updated["item"])
        self.assertFalse(self.call(1, "task", "get", {"id": item_id})["ok"])
        self.assertFalse(self.call(1, "task", "delete", {"id": item_id})["ok"])

    def test_user_and_kind_isolation_for_all_operations(self):
        task = self.call(1, "task", "create", {"fields": {"secret": "one"}})["item"]
        event = self.call(1, "event", "create", {"fields": {"secret": "event"}})["item"]
        other = self.call(2, "task", "create", {"fields": {"secret": "two"}})["item"]
        for user, kind, item_id in [(2, "task", task["id"]), (1, "event", task["id"]),
                                    (1, "task", event["id"]), (1, "task", other["id"])]:
            for action, extra in [("get", {}), ("delete", {}),
                                  ("update", {"fields": {"secret": "changed"}})]:
                result = self.call(user, kind, action, {"id": item_id, **extra})
                self.assertEqual(result, {"ok": False, "error": "item not found"})
        self.assertEqual([x["id"] for x in self.call(1, "task", "list", {})["items"]], [task["id"]])
        self.assertEqual([x["id"] for x in self.call(1, "event", "list", {})["items"]], [event["id"]])
        self.assertEqual(self.call(1, "task", "get", {"id": task["id"]})["item"]["fields"], {"secret": "one"})

    def test_pagination_and_empty_pages(self):
        ids = [self.call(1, "event", "create", {"fields": {"n": n}})["item"]["id"]
               for n in range(4)]
        page = self.call(1, "event", "list", {"limit": 2, "offset": 1})
        self.assertEqual([item["id"] for item in page["items"]], list(reversed(ids))[1:3])
        self.assertEqual((page["total"], page["limit"], page["offset"]), (4, 2, 1))
        self.assertEqual(self.call(1, "event", "list", {"offset": 20})["items"], [])

    def test_rejects_invalid_input_without_mutating(self):
        item = self.call(1, "task", "create", {"fields": {"x": 1}})["item"]
        cases = [
            (True, "task", "list", {}), (1, "bad", "list", {}),
            (1, "task", "bad", {}), (1, "task", "create", {}),
            (1, "task", "create", {"fields": []}),
            (1, "task", "create", {"fields": {"x": float("nan")}}),
            (1, "task", "create", {"fields": {"nested": {1: 2}}}),
            (1, "task", "get", {"id": True}), (1, "task", "list", {"limit": 101}),
            (1, "task", "list", {"offset": -1}), (1, "task", "list", {"filter": {}}),
            (1, "task", "update", {"id": item["id"]}),
            (1, "task", "update", {"id": item["id"], "delete_fields": "x"}),
            (1, "task", "update", {"id": item["id"], "fields": {"x": 2}, "delete_fields": ["x"]}),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertFalse(self.call(*args)["ok"])
        self.assertEqual(self.call(1, "task", "get", {"id": item["id"]})["item"], item)


if __name__ == "__main__":
    unittest.main()
