| Log state | Gate behaviour |
|---|---|
| Unretracted **HALT** anywhere | **Blocks** — checked first, over the whole log |
| **PASS** (matching, unconsumed) | Allows |
| **CONDITIONAL PASS**, `conditions_verified == "verified"` | Allows |
| **CONDITIONAL PASS**, conditions not verified | Blocks **"as if HALT"** |
| Row already consumed/used | Skipped — a clearance **cannot be replayed** |
