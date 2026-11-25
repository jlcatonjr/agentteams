| Profile | OS boundary | Emits |
|---|---|---|
| **`cooperative`** | None (`os_sandbox: "off"` is the dedicated off switch) | No sandbox request |
| **`confined`** | Requested — **today's default** | A sandbox request token |
| **`exclusive`** | Requested + read-exclusion | A sandbox request token **and** `denyRead` |
