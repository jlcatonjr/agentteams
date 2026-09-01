# Part VI — Integrity & drift  (SB18–SB19)

<!-- skeleton:SB18 SB19 -->

The subsystem tamper-tracks its own controls: `enforcement-integrity.json` pins a sha256 of the emitters
**and** the launcher asset (the flags that *are* the boundary live in the `.sh`, so pinning the `.py`
alone would be insufficient). An unintended edit trips `--verify-integrity` (red-team probe E4).

**Ceiling for a reviewer:** the content pin makes an *edit* tamper-evident, not tamper-proof, and it
protects only textual flags — bwrap's implicit `NoNewPrivs` has no line to diff, so a bwrap-version
change could drop it undetected. Cross-repo consumers keep a byte-identical copy under a sha pin; a
change is coordinated.

*Detail:* [Reference Part VI](../reference/part-vi-integrity-and-drift.md).
