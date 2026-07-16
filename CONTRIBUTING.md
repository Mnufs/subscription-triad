# Contributing

Contributions are welcome, especially around cross-platform behavior, provider CLI compatibility, state recovery, tests, and clearer failure messages.

## Development rules

1. Preserve subscription-only routing. Do not add API-key fallback behavior.
2. Preserve fail-closed approval and exact plan-hash validation.
3. When external agmsg is present, use only its public scripts; never read or mutate its internal database or team JSON. The separate embedded fallback must stay project-local and schema-isolated from agmsg.
4. Keep the Python runtime dependency-free unless a dependency solves a demonstrated reliability or security problem.
5. Add tests for every state transition, provider command change, and security boundary.
6. Do not include credentials or real provider transcripts in fixtures.

## Checks

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q plugins tests
```

When developing inside Codex, also run the plugin and Skill validators documented in the README.
