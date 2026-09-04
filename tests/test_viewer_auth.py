import importlib
import unittest


class TicketTests(unittest.TestCase):
    def authority(self):
        try:
            return importlib.import_module("realms.viewer_auth").Tickets
        except ModuleNotFoundError:
            self.fail(
                "Viewer capabilities need realm, generation, expiry and control scoping"
            )

    def test_scope_expiry_and_revocation(self):
        now = [10.0]
        tickets = self.authority()(clock=lambda: now[0])
        token = tickets.issue("realm-a", "generation-1", ttl=60, can_control=False)
        self.assertTrue(tickets.check(token, "realm-a", "generation-1"))
        self.assertIsNone(tickets.check(token, "realm-b", "generation-1"))
        self.assertIsNone(tickets.check(token, "realm-a", "generation-2"))
        self.assertIsNone(tickets.check(token, "realm-a", "generation-1", control=True))
        tickets.revoke("realm-a")
        self.assertIsNone(tickets.check(token, "realm-a", "generation-1"))
        token = tickets.issue("realm-a", "generation-1", ttl=60, can_control=True)
        self.assertTrue(tickets.check(token, "realm-a", "generation-1", control=True))
        now[0] = 71
        self.assertIsNone(tickets.check(token, "realm-a", "generation-1"))


if __name__ == "__main__":
    unittest.main()
