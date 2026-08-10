#!/usr/bin/env python3
"""Focused privacy and compatibility tests for SyntaxEventBus telemetry."""

from __future__ import annotations

import unittest

from SyntaxIntelligence.event_bus import SyntaxEventBus


class SecretObject:
    def __str__(self) -> str:
        return "SECRET_OBJECT_STRING"

    def __repr__(self) -> str:
        return "SECRET_OBJECT_REPR"


class TestSyntaxEventBusTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = SyntaxEventBus()

    def _log_text(self) -> str:
        return repr(self.bus.get_message_log())

    def test_publish_delivers_original_payload_but_redacts_log(self) -> None:
        secret = "SECRET_AUDIT_TEXT_7f8d"
        payload = {
            "task_id": "task-1",
            "context": {"audit_text": secret, "nested": {"secret": secret}},
        }
        received: list[dict] = []
        self.bus.subscribe("listener", "task.offered", lambda _a, _c, data: received.append(data))

        self.bus.publish("swarm", "task.offered", payload)

        self.assertIs(received[0], payload)
        self.assertEqual(received[0]["context"]["audit_text"], secret)
        log_text = self._log_text()
        self.assertNotIn(secret, log_text)
        self.assertNotIn("audit_text", log_text)
        publish_entries = [entry for entry in self.bus.get_message_log() if entry["type"] == "publish"]
        self.assertEqual(publish_entries[-1]["detail"], "[REDACTED]")

    def test_broadcast_delivers_original_payload_but_redacts_log(self) -> None:
        secret = "SECRET_BROADCAST_TEXT_9a2b"
        payload = {"context": {"audit_text": secret}}
        received: list[dict] = []
        self.bus.subscribe("listener-a", "one", lambda _a, _c, data: received.append(data))
        self.bus.subscribe("listener-b", "two", lambda _a, _c, data: received.append(data))

        self.bus.broadcast("swarm", "global", payload)

        self.assertEqual(received, [payload, payload])
        self.assertNotIn(secret, self._log_text())
        self.assertNotIn("audit_text", self._log_text())
        broadcast_entries = [entry for entry in self.bus.get_message_log() if entry["type"] == "broadcast"]
        self.assertEqual(broadcast_entries[-1]["detail"], "[REDACTED]")

    def test_hostile_payload_values_are_never_stringified(self) -> None:
        payload = {"context": {"audit_text": SecretObject()}}
        self.bus.publish("swarm", "task.offered", payload)

        log_text = self._log_text()
        self.assertNotIn("SECRET_OBJECT_STRING", log_text)
        self.assertNotIn("SECRET_OBJECT_REPR", log_text)
        self.assertNotIn("audit_text", log_text)

    def test_publish_and_broadcast_preserve_sender_suppression(self) -> None:
        received: list[tuple[str, str]] = []
        self.bus.subscribe("sender", "task.offered", lambda agent, channel, _data: received.append((agent, channel)))
        self.bus.subscribe("other", "task.offered", lambda agent, channel, _data: received.append((agent, channel)))

        self.bus.publish("sender", "task.offered", {"value": 1})
        self.bus.broadcast("sender", "task.offered", {"value": 2})

        self.assertEqual(received, [("other", "task.offered"), ("other", "task.offered")])

    def test_concurrent_publishers_produce_complete_log_entries(self) -> None:
        import threading

        barrier = threading.Barrier(8)

        def publish_many(index: int) -> None:
            barrier.wait()
            for sequence in range(25):
                self.bus.publish(f"sender-{index}", "status", {"sequence": sequence})

        threads = [threading.Thread(target=publish_many, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        publish_entries = [entry for entry in self.bus.get_message_log() if entry["type"] == "publish"]
        self.assertEqual(len(publish_entries), 50)
        self.assertTrue(all(entry["detail"] == "[REDACTED]" for entry in publish_entries))
        self.assertTrue(all(set(entry) == {"timestamp", "type", "agent_id", "channel", "detail", "msg_id"} for entry in publish_entries))

    def test_callback_exception_does_not_log_exception_text(self) -> None:
        secret = "SECRET_EXCEPTION_TEXT_1c3e"

        def failing_callback(_agent: str, _channel: str, _data: dict) -> None:
            raise RuntimeError(secret)

        self.bus.subscribe("listener", "task.offered", failing_callback)
        self.bus.publish("swarm", "task.offered", {"context": {"audit_text": secret}})

        log_entries = self.bus.get_message_log()
        self.assertNotIn(secret, repr(log_entries))
        errors = [entry for entry in log_entries if entry["type"] == "error"]
        self.assertEqual(errors[-1]["detail"], "RuntimeError")

    def test_log_snapshot_entries_are_independent(self) -> None:
        self.bus.publish("swarm", "status", {"state": "ok"})
        snapshot = self.bus.get_message_log()
        snapshot[-1]["detail"] = "tampered"

        fresh = self.bus.get_message_log()
        self.assertNotEqual(fresh[-1]["detail"], "tampered")

    def test_legacy_log_hook_remains_thread_safe_and_flat(self) -> None:
        self.bus._log("validation_blocked", "validator", "task.offered", "safe detail")

        entry = self.bus.get_message_log()[-1]
        self.assertEqual(entry["type"], "validation_blocked")
        self.assertEqual(entry["detail"], "safe detail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
