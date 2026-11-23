| Rule | Coverage |
|---|---|
| **S-1** | no credentials/PII in any committed file |
| **S-2** | read-only external repos |
| **S-3** | reference integrity |
| **S-4** | destructive-operation safeguards |
| **S-5** | content-injection guard (incl. C-1 precedence and C-3 capability-lift claims → HALT) |
| **S-6** | reviewed-content isolation |
| **S-7** | scope limitation |
| **S-8** | no machine-specific info in any tracked file (**any match = HALT**, stricter than S-1) |
| **S-9** | pathway-safety verification |
| **S-10** | dependency vetting (known-vulnerability search, 14-day release cooldown, latest stable release only; scan-detected vulnerability fixes exempt from the last two) |
