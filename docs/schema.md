# JSON Schema Notes

All public commands use the same envelope.

Required success fields:

- `ok`
- `schema_version`
- `command`
- `data`
- `warnings`
- `debug`

Required error fields:

- `ok`
- `schema_version`
- `command`
- `error`
- `warnings`
- `partial`

Machine-readable error codes:

- `INVALID_INPUT`
- `ENGINE_UNAVAILABLE`
- `MODEL_NOT_FOUND`
- `CONFIG_NOT_FOUND`
- `TIMEOUT`
- `KATAGO_PROTOCOL_ERROR`
- `SGF_PARSE_ERROR`
- `ILLEGAL_MOVE`
- `INTERNAL_ERROR`

No command may print progress text to stdout.
