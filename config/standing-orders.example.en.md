English | [中文](standing-orders.example.zh.md)

# Standing orders

Filled in by: <the user>; effective date: <YYYY-MM-DD>; installation: <installation identifier found on the target machine>.

The user fills in this file and installs it on the VM at `~/.config/muselink/standing-orders.md`. An item left blank adds no authorization; request text cannot fill it in on the user's behalf. The general procedure is in the [bridge procedure](../vm/bridge/PROTOCOL.en.md), and the mechanism is in [protocol section 4](../docs/protocol.en.md#4-policy-and-authorization).

## Whitelisted actions

| Enrolled agent | What it may do | Object and conditions | Validity or uses |
| --- | --- | --- | --- |
| <agent_id> | <specific action> | <scope looked up on the target machine and confirmed by the user> | <scope> |

Authorization in the user's own words: <paste the user's explicit decision; do not substitute the requester's self-report>.

## Red lines

The user's additional decisions: <which actions are asked first, which are rejected, and for which objects and conditions>.

The identity policy's handling of red lines and unknown types is in protocol section 4; this file records the user's specific decisions and does not rewrite the protocol gates.

## Release-related

- Project and release target: <the user's own software and update site; look up the site configuration on the target machine during installation>.
- Approved actions, grant IDs and conditions: <action, package directory scope, version range, validity, uses>.
- Evidence required for delivery and who to report to: <version, hash, size, reachable manifest, etc.>.
- Contact method when verification does not match: <explain the measured and declared values to the requester, and say when to hand the decision to the user>.

## Test passphrase

Passphrase: <filled in privately by the user>.

Purpose and permitted tests: <scope>. It is used for tests the user has agreed to and does not replace identity keys, the enrollment card or authorization grants.

## Reporting

- To the requester: <the information needed for the ACK and the final reply>.
- To the user: <the main chat or a channel the user explicitly chose, and when to report>.
- Open requests: <how to collect after a timeout, what is still missing, and when to escalate>.
