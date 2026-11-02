# Part II — The request  (SB4–SB6)

<!-- skeleton:SB4 SB5 SB6 -->

Confinement is requested by a `privilege_profile` (`confined` = write-confinement; `exclusive` adds
read-exclusion of credentials + sibling workspaces) **or** a `*:sandbox` host-feature token. Two
review-relevant safety properties:

1. **A typo fails closed.** An unknown profile value hard-errors at parse; it cannot silently ship a
   `cooperative` team that *looks* confined.
2. **A token with no emitter is rejected.** `*:sandbox` for a namespace that emits nothing (bridge
   namespaces) is refused — a *validating* token that confined nothing would be exactly the
   false-confidence signal the subsystem exists to avoid.

3. **The OS switch is governed.** `os_sandbox: "off"` (or `--os-sandbox off`) suppresses the boundary
   independently of the profile; because that relaxes a constraint, a governed workspace (strict decision
   signing, the default) refuses it without an operator Ed25519-signed decision
   `sandbox-os-off:<framework>`, and every other workspace prints `SANDBOX RELAXATION`.

The request lives entirely in the manifest; there is no out-of-band sandbox state to audit separately.

*Detail:* [Reference Part II](../reference/part-ii-the-request.md).
