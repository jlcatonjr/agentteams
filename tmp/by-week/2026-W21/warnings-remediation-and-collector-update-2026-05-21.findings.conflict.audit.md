# Conflict Audit — Warnings Findings (2026-05-21)

Scope:
- Findings report + adversarial notes
- Existing module protocol and audit semantics

Checks:

1. Check: preserving manual placeholder warning aligns with governance model.
- Result: PASS.

2. Check: CH14 marker-based calibration preserves audit intent.
- Result: PASS WITH NOTES.
- Notes: marker must be explicit and narrow to avoid weakening hygiene coverage.

3. Check: proposed change avoids cross-domain governance conflicts.
- Result: PASS.

4. Check: remediation remains compatible with self-update merge workflow.
- Result: PASS.

Conflict verdict:
- STATUS: PASS WITH NOTES
- Approved remediation target: CH14 noise reduction via explicit allow markers.
