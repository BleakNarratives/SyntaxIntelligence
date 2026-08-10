# Syntax Event-Bus Telemetry Hardening

## Status

**Implemented in `SyntaxIntelligence/event_bus.py`; retained as the design and
verification record.**

This document specifies and records the implementation that prevents sensitive
task context, especially `context.audit_text`, from entering the in-memory
`SyntaxEventBus` message log. Subscriber delivery and the task protocol remain
unchanged.

## 1. Problem and security objective

Before hardening, `SyntaxEventBus.publish()` and `SyntaxEventBus.broadcast()`
logged a string representation of the event payload before invoking
subscribers. The
Syntax-to-OutClaw adapter needs the original task payload in order to perform
its audit, but raw legal text must not persist in:

- `SyntaxEventBus._message_log`;
- `get_message_log()` responses;
- dashboard/API views backed by `get_message_log()`;
- callback-error log entries;
- debug output derived from the event bus.

### Security objective

For every event-bus ingress path, no raw value from a sensitive field may be
written to the in-memory telemetry log. In particular, a secret sentinel placed
in `payload["context"]["audit_text"]` must never appear anywhere in a stored
message-log entry, including nested values or stringified representations.

This guarantee applies to the event-bus log only. The event payload still exists
in the caller and is intentionally delivered to authorized subscribers. Python
cannot erase a caller's original string from every object that may reference it;
the boundary here is preventing persistence and exposure through event-bus
telemetry.

## 2. Current flow and required boundary

Implemented flow:

```text
caller payload
    |
    +--> append fixed metadata + "[REDACTED]" to _message_log
    |
    +--> subscribers receive original payload unchanged
```

The implemented flow uses a fixed marker rather than a serialized payload:

```text
caller payload
    |
    +--> append flat envelope metadata + "[REDACTED]" to _message_log
    |
    +--> subscribers receive original payload unchanged
```

Redaction must happen inside the event bus, at the start of `publish()` and
`broadcast()`, before any call to `str()`, `repr()`, formatting, logging, or
message-log append. Redacting only in `OutClawTaskAdapter` is insufficient
because the event bus observes the payload first.

## 3. Proposed contract

### 3.1 Delivery/log separation

`publish()` and `broadcast()` must maintain two distinct objects:

1. **Delivery payload** — the original `data` object passed to eligible
   subscribers for backward compatibility and adapter operation. Preserve the
   current routing semantics: `publish()` does not echo to the sender, while
   `broadcast(sender_id, channel, data)` delivers to subscribers across all
   subscribed channels except the sender.
2. **Log projection** — a newly created, sanitized metadata object used only for
   `_message_log`.

The sanitizer must never mutate the delivery payload. This prevents telemetry
hardening from changing adapter behavior or causing one subscriber to receive a
redacted object after another subscriber has already processed the original.

### 3.2 Metadata-only default

The safest default for a generic event bus is to log envelope metadata rather
than arbitrary payload values:

```text
{
  "event": "publish",
  "sender_id": "swarm",
  "channel": "task.offered",
  "msg_id": 42,
  "payload": "redacted",
  "redacted": true
}
```

Do not use `str(data)`, `repr(data)`, JSON serialization of the original
payload, or truncation as a privacy mechanism. Truncation can still expose the
beginning of a legal document or a secret embedded in a nested value.

Envelope-only logging is the normative rule for this design. If a future
operator requests selected fields for diagnostics, that is a separate design
review requiring an explicit, per-channel safe allowlist. There must be no
general-purpose "log all fields except these names" fallback.

### 3.3 Sensitive-field policy

For any optional structured projection, field matching should be:

- case-insensitive;
- tolerant of equivalent separator styles (`audit_text`, `audit-text`, and
  `auditText` if that convention is introduced);
- recursive through mappings and sequences;
- applied to the full path, not only the leaf name.

At minimum, the deny policy must cover:

- `context.audit_text`;
- raw document/content fields such as `raw_text`, `document_text`,
  `source_text`, `content`, and `body` when they contain user material;
- legal-text aliases added by future adapters.

However, the default metadata-only projection should omit all payload values,
so a missed alias cannot leak through the generic event-bus path.

### 3.4 Fail-closed handling

The implemented log path fails closed by never inspecting payload values:

- publish/broadcast entries use the fixed detail marker `"[REDACTED]"`;
- mappings, sequences, and unknown objects are never traversed or stringified;
- hostile or secret-bearing `__str__`/`__repr__` methods cannot run through
  event payload logging;
- callback exceptions are logged by exception type and stable status only, not
  by `str(exception)`, because exception text can echo the original payload.

A richer recursive projection with safe key/path metadata remains future design
work and is not part of the current implementation.

The event bus continues delivering the original payload because telemetry
never classifies or traverses it. Observability may lose detail; privacy must
not be traded for diagnostics.

All internal event-bus appends use the event-bus lock, including publish,
broadcast, subscription, unsubscription, scheduler, callback-error, and
cron-error entries. The internal `_append_log_locked()` helper makes that
ownership explicit. The pre-existing `_log()` compatibility hook is intentionally
unchanged, remains a trusted non-payload path, and must not receive raw task,
legal, or user-provided text; external callers retain responsibility for using
it safely.

## 4. Implementation plan

### Phase A — Implement the private log-projection boundary

The implementation uses a fixed telemetry projection at the event-bus ingress
points. `publish()` and `broadcast()` append flat envelope metadata with the
constant detail value `"[REDACTED]"`; they never inspect, traverse, stringify,
or serialize the payload. Internal callers holding the bus lock use
`_append_log_locked()` for atomic append operations.

The existing `_log()` helper remains unchanged as a trusted compatibility hook
for validator and scheduler summaries. It must receive only already-classified,
non-payload detail; event payloads must never be routed through it.

### Phase B — Route every ingress path through the boundary

Both event paths now use the boundary consistently. The APIs are:

- `SyntaxEventBus.publish(sender_id, channel, data)`, which delivers to channel
  subscribers except the sender;
- `SyntaxEventBus.broadcast(sender_id, channel, data)`, which delivers across
  all subscribed channels except the sender.

Both methods must preserve those routing semantics while changing only the
telemetry representation.

For each path:

1. acquire the event-bus lock;
2. increment the message counter;
3. copy subscriber callbacks while holding the lock;
4. construct the safe log projection without stringifying `data`;
5. append the projection while holding the same lock;
6. release the lock;
7. invoke callbacks with the original payload.

The same locked append boundary applies to `_log()` calls made by subscribe,
unsubscribe, scheduler setup, callback-error handling, and cron-error handling.

No event path should log the original payload before or after callback delivery.

### Phase C — Harden secondary log paths

The callback and scheduler error paths use the same locked append boundary and
record exception type only; exception messages are never persisted.

Audit all other event-bus logging calls:

- subscription and unsubscription messages;
- scheduler registration messages;
- callback exception messages;
- cron callback errors;
- dashboard/API serialization of `get_message_log()`.

Callback and scheduler failures should record stable metadata such as:

```text
{
  "event": "callback_error",
  "agent_id": "outclaw-auditor",
  "channel": "task.offered",
  "error_type": "RuntimeError"
}
```

Never persist exception text by default. If an operational need later requires
error details, add a separate reviewed redaction policy rather than reusing
`str(exception)`.

### Phase D — Protect the read boundary

`get_message_log()` should return a snapshot, not a mutable view of internal
entries. Prefer a deep copy or immutable-safe projection so callers cannot:

- mutate the bus's internal log;
- insert raw values into an existing entry;
- retain references to internal nested objects.

This is defense in depth. The primary guarantee remains that raw sensitive data
was never inserted at ingress.

### Phase E — Document the privacy contract

The event-bus module, README, RULEZ, runbook, and this design record now state
that:

- subscribers receive the original event payload;
- in-memory telemetry is metadata-only by default;
- sensitive payload values are not recoverable through `get_message_log()`;
- event-bus telemetry is not a substitute for a secure audit archive;
- raw payload retention, if ever required, needs a separate access-controlled
  storage design and explicit approval.

## 5. Test matrix

Tests should be added alongside the event-bus tests when implementation begins.
The following cases are required for acceptance.

### A. Positive delivery / negative persistence

1. Publish a `task.offered` payload containing a unique secret in
   `context.audit_text`.
2. Assert the subscriber receives the exact original text.
3. Assert `get_message_log()` contains neither the secret nor the raw context.
4. Assert the secret does not appear in `str(log)`, `repr(log)`, or nested log
   values.

Repeat the same test for `broadcast()`.

### B. Nested and adversarial values

Cover:

- `context.audit_text` nested several levels deep;
- lists and mappings containing the secret;
- multiple sensitive fields;
- a custom object whose `__str__` and `__repr__` raise or return a secret;
- a payload with non-JSON values;
- an empty context and a legacy payload without context.

The event must still reach subscribers, while the log remains safe.

### C. Error paths

1. Use a subscriber that raises an exception containing the secret.
2. Assert the event bus records only safe error metadata, such as exception
   type.
3. Assert the secret does not appear in the message log.

Repeat for a scheduler callback failure if the scheduler uses the same log.

### D. Compatibility and concurrency

1. Existing non-sensitive publish/broadcast behavior remains unchanged for
   subscribers.
2. Existing message counters and channel subscriber statistics remain correct.
3. Concurrent publishers never append partially constructed log entries.
4. A subscriber mutating its received payload cannot mutate the stored log
   projection.
5. `get_message_log()` returns an independent snapshot.

### E. Regression guard

Add a test that fails if production code reintroduces `str(data)` or `repr(data)`
for event payload logging. A behavior test using a secret-bearing object is more
valuable than a text-only grep, but both can be useful as defense in depth.

## 6. Acceptance criteria

The hardening work is complete only when all of the following are true:

- No raw event payload is stringified for message-log purposes.
- `context.audit_text` never appears in `_message_log` or any
  `get_message_log()` result.
- Subscribers still receive the unmodified payload required by
  `OutClawTaskAdapter`.
- `publish()` and `broadcast()` have equivalent privacy behavior.
- Callback and scheduler exception logs cannot echo exception text.
- Unknown/non-serializable payload objects fail closed.
- Log reads return independent snapshots.
- Focused telemetry tests and the existing Syntax/OutClaw regression suites
  pass.
- The event-bus privacy contract is documented for operators.

## 7. Rollout and rollback

### Rollout status

1. Implemented inside `SyntaxEventBus`; the task protocol was not changed.
2. Focused telemetry tests pass, including payload delivery, sender suppression,
   hostile values, exception redaction, snapshot isolation, and concurrency.
3. Syntax, adapter, validator, handoff, dispatcher, OutClaw, and bridge suites
   pass.
4. Dashboard/API consumers of `get_message_log()` continue to receive flat
   envelope metadata without payload content.
5. No debug flag restores raw payload logging. The trusted `_log()` compatibility
   hook remains the only caller-supplied detail path and must stay payload-free.

### Rollback posture

If a compatibility issue appears, roll back the implementation commit rather
than adding a raw-payload escape hatch. A rollback may restore the known privacy
risk and must therefore be treated as a temporary incident with explicit owner,
scope, and expiry.

## 8. Non-goals and open decisions

This design does not address:

- redacting raw legal text from caller-owned task objects;
- securing the OutClaw findings archive;
- encrypting event-bus transport;
- access control for subscribers;
- removing already-leaked values from historical logs or process memory;
- a general PII classifier.

Before implementation, decide whether the log should retain any safe payload
metadata beyond envelope fields (for example, task ID, capability names, or
context key names). The recommended default is to omit context key names too
when they could reveal case-sensitive operational details, and to log only
stable envelope metadata plus a redacted marker.

## 9. Implemented slice

The bounded implementation:

1. replaces payload stringification in `publish()` and `broadcast()` with the
   fixed `[REDACTED]` marker;
2. suppresses exception text in callback and scheduler error logs;
3. adds publish/broadcast secret-sentinel tests proving delivery is preserved
   and message-log persistence is clean;
4. adds hostile-object, sender-routing, snapshot, and concurrency coverage;
5. passes the established regression suites.

This delivers the zero-leakage event-bus boundary without changing the
`TaskOffer` contract, the OutClaw adapter, or cross-device synchronization.

## 10. Implementation record

Implemented files:

- `SyntaxIntelligence/event_bus.py` — metadata-only publish/broadcast logging,
  exception-type logging, locked internal append helper, and independent log
  snapshots; the pre-existing `_log()` hook remains unchanged.
- `SyntaxIntelligence/test_event_bus_telemetry.py` — eight focused privacy,
  routing, snapshot, compatibility, and concurrency tests.

The compatibility `_log()` hook was intentionally preserved. It is trusted
infrastructure, not a payload redaction boundary; callers must pass summaries
that contain no raw task or legal text.
