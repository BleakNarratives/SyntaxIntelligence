"""
Sync bridge tests -- egress, ingress, idempotency, writer-owns isolation,
TTL cleanup, cross-device self-echo guard.

Runs entirely in tempdirs; no real rclone needed. These tests assume the
bus module's `SyntaxEventBus` is importable; if it's not (e.g. in pure
unit-test mode), SyncBridge still operates in fallback mode and the
publisher / subscriber tests exercise the file queue side directly.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

# Use isolated test paths so we don't pollute the real sync_bus.
os.environ.setdefault("BUS_SYNC_ROOT", "/tmp/__sync_bridge_test__")

from SyntaxIntelligence.sync_bridge import SyncBridge, BridgeEnvelope, dict_to_envelope  # noqa: E402


def _now() -> float:
    return float(os.environ.get("BUS_TEST_NOW") or time.time())


def _bootstrap_devices(root: Path, ids):
    for did in ids:
        (root / did).mkdir(parents=True, exist_ok=True)
        (root / did / "messages").mkdir(exist_ok=True)


class TestEgress(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        _bootstrap_devices(root, ["me", "peer1"])
        self.bridge = SyncBridge(sync_root=root, device_id="me")

    def test_publish_local_writes_file(self):
        env = self.bridge.publish_local("bus.bridge", {"hello": 1}, sender_id="me.bot")
        self.assertIsNotNone(env)
        self.assertEqual(env.origin_device, "me")
        path = self.bridge._my_messages / f"{int(env.timestamp)}_{env.message_id}.json"
        self.assertTrue(path.exists())
        body = json.loads(path.read_text())
        self.assertEqual(body["channel"], "bus.bridge")
        # ``publish_local`` writes CLEAN payload only -- it does NOT forward
        # to the local bus (loop-prevention contract).
        self.assertEqual(body["payload"], {"hello": 1})
        self.assertNotIn("_bridge_origin", body["payload"])

    def test_publish_local_does_not_loop_via_local_bus(self):
        """
        Critical loop-prevention test: even when the local Syntax bus is
        alive and the bridge is listening, calling publish_local must
        produce exactly ONE disk file, not a cascade.
        """
        # Force the bridge to interact with the in-process bus if available.
        if self.bridge._local_bus is not None:
            captured = []
            self.bridge._local_bus.subscribe("loop-capture", "bus.bridge",
                                             lambda a, c, d: captured.append(d))
        env = self.bridge.publish_local("bus.bridge", {"only_one": True})
        files = list(self.bridge._my_messages.glob("*.json"))
        self.assertEqual(len(files), 1, f"Expected 1 disk write, got {len(files)}: {files}")
        # If a local bus was wired, ensure NO listener was fired for this
        # event (publish_local does not re-broadcast).
        if self.bridge._local_bus is not None:
            self.assertEqual(captured, [])

    def test_egress_filter_blocks_replay(self):
        # If a payload already tagged as ingress from another device, filter rejects.
        payload = {"_bridge_origin": "ingress", "channel": "bus.bridge", "from": "peer1"}
        envelope = dict_to_envelope(payload, source_device="peer1")
        self.assertFalse(self.bridge._default_egress_filter(envelope.to_dict()))

    def test_dict_to_envelope_picks_up_callback_channel(self):
        # Regression: when a raw agent publishes via local_bus with channel
        # "outclaw.findings", data doesn't carry ``channel``, so the envelope
        # copy must pick up the channel from the callback arg, not default
        # to "bus.bridge".
        payload = {"high_count": 2, "safe_to_draft": False}
        envelope = dict_to_envelope(payload, source_device="me", channel="outclaw.findings")
        self.assertEqual(envelope.channel, "outclaw.findings")
        self.assertEqual(envelope.payload, payload)

    def test_dict_to_envelope_data_channel_overrides_callback(self):
        # If the data already carries ``channel``, prefer it (envelopes
        # round-trip this way).
        payload = {"channel": "outclaw.draft_blocked", "v": 1}
        envelope = dict_to_envelope(payload, source_device="me", channel="bus.bridge")
        self.assertEqual(envelope.channel, "outclaw.draft_blocked")


class TestIngress(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        _bootstrap_devices(root, ["me", "peer"])
        self.bridge = SyncBridge(sync_root=root, device_id="me")
        self.captured = []
        self.bridge.add_subscriber("bus.bridge", lambda env: self.captured.append(env))

    def _drop_peer_message(self, *, mid, sender="peer.bot", channel="bus.bridge",
                           origin="peer", ts=None, payload=None):
        env = BridgeEnvelope(
            message_id=mid,
            sender_id=sender,
            channel=channel,
            payload=payload or {"v": 1},
            timestamp=ts if ts is not None else time.time(),
            origin_device=origin,
        )
        path = self.bridge._sync_root / "peer" / "messages" / f"{int(env.timestamp)}_{mid}.json"
        path.write_text(json.dumps(env.to_dict()))

    def test_ingress_dispatches_subscriber_once(self):
        self._drop_peer_message(mid="m1", ts=time.time())
        self.bridge._poll_once()
        self.assertEqual(len(self.captured), 1)
        # Re-poll: should NOT dispatch again (idempotency).
        self.bridge._poll_once()
        self.assertEqual(len(self.captured), 1)

    def test_ingress_skips_self_echo(self):
        # Authored by 'me' but lives in my own outbox (e.g. mid-sync race).
        env = BridgeEnvelope(
            message_id="self-echo",
            sender_id="me.bot",
            channel="bus.bridge",
            payload={"v": 1},
            timestamp=time.time(),
            origin_device="me",
        )
        path = self.bridge._my_messages / f"{int(env.timestamp)}_self-echo.json"
        path.write_text(json.dumps(env.to_dict()))
        self.bridge._poll_once()
        # Dispatched to me subscribers? Should NOT be -- origin_device==me.
        self.assertEqual(self.captured, [])

    def test_ingress_skips_existing_seen(self):
        # First poll -- dispatch.
        self._drop_peer_message(mid="dup1", ts=time.time())
        self.bridge._poll_once()
        self.assertEqual(len(self.captured), 1)
        # Mark seen manually, then re-poll.
        self.bridge._mark_seen("dup1")
        self.bridge._poll_once()
        self.assertEqual(len(self.captured), 1)


class TestWriterOwns(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _bootstrap_devices(self.root, ["a", "b"])

    def test_each_bridge_only_writes_own_directory(self):
        bridge_a = SyncBridge(sync_root=self.root, device_id="a")
        bridge_b = SyncBridge(sync_root=self.root, device_id="b")
        bridge_a.publish_local("bus.bridge", {"from": "a"})
        bridge_b.publish_local("bus.bridge", {"from": "b"})
        a_files = list((self.root / "a" / "messages").glob("*.json"))
        b_files = list((self.root / "b" / "messages").glob("*.json"))
        self.assertGreaterEqual(len(a_files), 1)
        self.assertGreaterEqual(len(b_files), 1)
        # Writer-owns: each message's origin_device matches its directory.
        for f in a_files:
            body = json.loads(f.read_text())
            self.assertEqual(body["origin_device"], "a")
        for f in b_files:
            body = json.loads(f.read_text())
            self.assertEqual(body["origin_device"], "b")


class TestHeartbeat(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        _bootstrap_devices(root, ["me", "peer"])
        self.bridge = SyncBridge(sync_root=root, device_id="me")

    def test_presence_written_on_update(self):
        self.bridge._update_presence()
        body = json.loads((self.bridge.sync_root / "me" / "presence.json").read_text())
        self.assertEqual(body["device_id"], "me")
        self.assertIn("last_seen", body)
        self.assertIn("hostname", body)


class TestTTLCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        _bootstrap_devices(root, ["me"])
        self.bridge = SyncBridge(sync_root=root, device_id="me")

    def test_old_messages_deleted(self):
        # Drop an old message (timestamp = 1 epoch second).
        env = BridgeEnvelope(
            message_id="old",
            sender_id="me.bot",
            channel="bus.bridge",
            payload={"v": 1},
            timestamp=1.0,
            origin_device="me",
        )
        path = self.bridge._my_messages / f"1_old.json"
        path.write_text(json.dumps(env.to_dict()))
        removed = self.bridge._cleanup_local_expired()
        self.assertEqual(removed, 1)
        self.assertFalse(path.exists())

    def test_recent_messages_left_alone(self):
        env = BridgeEnvelope(
            message_id="new",
            sender_id="me.bot",
            channel="bus.bridge",
            payload={"v": 1},
            timestamp=time.time(),
            origin_device="me",
        )
        path = self.bridge._my_messages / f"{int(env.timestamp)}_new.json"
        path.write_text(json.dumps(env.to_dict()))
        removed = self.bridge._cleanup_local_expired()
        self.assertEqual(removed, 0)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
