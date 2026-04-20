import unittest
from unittest.mock import patch

import app as app_module


app = app_module.app


class FakeResult:
    def __init__(self, row=None, lastrowid=None):
        self.row = row
        self._lastrowid = lastrowid

    def fetchone(self):
        return self.row

    @property
    def lastrowid(self):
        return self._lastrowid


class FakeConnection:
    def __init__(self, user_row):
        self.user_row = user_row
        self.queries = []
        self.committed = False
        self.closed = False
        self.next_id = 1

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if "FROM users WHERE username = ? OR email = ?" in query:
            return FakeResult(self.user_row)
        if "INSERT INTO admin_notifications" in query or "INSERT INTO system_logs" in query:
            row_id = self.next_id
            self.next_id += 1
            return FakeResult(lastrowid=row_id)
        if "SELECT id FROM admin_notifications WHERE id = ?" in query or "SELECT id FROM system_logs WHERE id = ?" in query:
            return FakeResult({"id": params[0]})
        return FakeResult()

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class SmokeTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_public_pages_load(self):
        for path in ("/", "/track", "/login", "/forgot-password", "/api/public-commuter"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertLess(response.status_code, 500)

    def test_admin_redirects_to_login(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_login_requires_csrf(self):
        response = self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.assertEqual(response.status_code, 400)

    def test_forgot_password_posts_admin_notification_for_username(self):
        fake_conn = FakeConnection(
            {
                "id": 1,
                "username": "admin",
                "email": "admin@example.com",
                "full_name": "Admin User",
                "role": "admin",
            }
        )
        get_response = self.client.get("/forgot-password")
        html = get_response.get_data(as_text=True)
        csrf_token = html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

        with patch.object(app_module, "get_db", return_value=fake_conn), patch.object(app_module, "broadcast_live_tracking_update"):
            response = self.client.post(
                "/forgot-password",
                data={"csrf_token": csrf_token, "account_identifier": "admin"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(fake_conn.committed)
        self.assertTrue(fake_conn.closed)
        self.assertTrue(
            any(
                "INSERT INTO admin_notifications" in query
                and params[0] == app_module.PASSWORD_RESET_NOTIFICATION_TYPE
                and params[1] == 1
                for query, params in fake_conn.queries
            )
        )


if __name__ == "__main__":
    unittest.main()
