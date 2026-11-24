| ID | Failure mode it catches |
|---|---|
| **F-1** | a verifier that always passes — every verifier needs a sensitivity test and a negative control |
| **F-2** | a fix wired to one of two call sites — every fix must reach every call path |
| **F-3** | hand-rolling what the tool provides — no hand-rolled target, descriptor, or VCS resolution |
| **F-4** | a coverage claim with an unexamined denominator — every count carries the population it was computed over |
| **F-5** | a probe that got blinder, not better — every probe whose behaviour changed is re-validated for intent |
| **F-6** | accepting a weakness with no ledger diff — every accepted weakness has a named reason |
