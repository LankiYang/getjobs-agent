"""Agent tool dispatch and explicit profile field deletion."""
import contextlib
import sqlite3
import unittest
from unittest.mock import patch

from app import agent


class AgentWorkItemToolsTest(unittest.TestCase):
    def test_profile_delete_is_scoped_and_preserves_other_fields(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE agent_profiles (user_id INTEGER PRIMARY KEY, data TEXT, updated_at TEXT)")

        @contextlib.contextmanager
        def connection():
            with conn:
                yield conn

        try:
            with patch.object(agent, "get_conn", connection):
                agent.save_profile_data(1, {"name": "A", "phone": "123", "skills": ["Python"]})
                agent.save_profile_data(2, {"phone": "456"})
                result = agent.execute_tool(1, "delete_profile_fields", {"fields": ["phone"]})
                self.assertEqual(result["profile"], {"name": "A", "skills": ["Python"]})
                self.assertEqual(agent.profile_data(1), result["profile"])
                self.assertEqual(agent.profile_data(2), {"phone": "456"})
                self.assertIn("error", agent.execute_tool(1, "delete_profile_fields", {"fields": ["name", 1]}))
                self.assertEqual(agent.profile_data(1), result["profile"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
