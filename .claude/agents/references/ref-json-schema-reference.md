<!-- AGENTTEAMS:BEGIN content v=1 -->
# JSON Schema Reference — AgentTeamsModule

> Quick-reference for **JSON Schema draft-2020-12** (library) in AgentTeamsModule.
> This is a lightweight reference file, not an agent. For operational procedures, consult the tool's reference/skill document, or escalate to `@orchestrator`.

---

## Version

`JSON Schema` `draft-2020-12`

## Configuration

**Config files:** `N/A`

## Official Documentation

https://json-schema.org/understanding-json-schema/

## Key API Surface

- **Type keywords** — `"type": "object"|"array"|"string"|"number"|"integer"|"boolean"|"null"`
- **Object keywords** — `"properties"`, `"required"`, `"additionalProperties"`, `"patternProperties"`, `"minProperties"`, `"maxProperties"`
- **Array keywords** — `"items"`, `"prefixItems"` (draft-2020-12), `"minItems"`, `"maxItems"`, `"uniqueItems"`, `"contains"`
- **String keywords** — `"minLength"`, `"maxLength"`, `"pattern"` (ECMA regex), `"format"` (informational by default)
- **Number keywords** — `"minimum"`, `"maximum"`, `"exclusiveMinimum"`, `"exclusiveMaximum"`, `"multipleOf"`
- **Schema composition** — `"allOf"`, `"anyOf"`, `"oneOf"`, `"not"`, `"if"/"then"/"else"`
- **Reuse** — `"$defs"` (draft-2019+; replaces `"definitions"`), `"$ref": "#/$defs/MyType"`
- **Meta-data** — `"title"`, `"description"`, `"default"`, `"examples"`, `"deprecated"`
- **Draft declaration** — `"$schema": "https://json-schema.org/draft/2020-12/schema"`

<!-- Document the primary classes, functions, or APIs that project code depends on from JSON Schema. -->

## Common Patterns & Pitfalls

- **`"additionalProperties": false`** — locks the schema; combine with `"required"` to enforce the full object shape
- **`"$defs"` for reusable types** — define shared sub-schemas in `"$defs"` and reference with `"$ref": "#/$defs/TypeName"`; avoids duplication
- **`"oneOf"` vs `"anyOf"`** — `oneOf` means exactly one sub-schema matches (XOR); `anyOf` means at least one; prefer `anyOf` for union types
- **Draft-2020-12 `"prefixItems"`** — replaces draft-07 `"items"` for positional tuple schemas; `"items"` in 2020-12 matches only items beyond the prefix
- **`"format"` is annotation-only by default** — `"format": "uri"` does not validate unless the validator is explicitly configured to enforce formats
- **`"required"` is independent of `"properties"`** — a property can appear in `"properties"` without being in `"required"`; omit it to make it optional
- **Keep `"$ref"` chains shallow** — circular refs are valid but require a validator that handles them; keep definitions one level deep in `"$defs"`

<!-- Document common usage patterns, best practices, and known issues for JSON Schema draft-2020-12. -->

## Key Conventions

- Follow project style rules when using JSON Schema
- Refer to authority sources for API contract accuracy
- Validate changes against existing tests before committing

## Related Agents

- `@technical-validator` — verify technical accuracy of JSON Schema usage
- `@primary-producer` — implements code that depends on JSON Schema
<!-- AGENTTEAMS:END content -->
