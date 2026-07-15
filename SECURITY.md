# Security policy

## Supported versions

Security fixes are applied to the latest release on the default branch.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities involving credential exposure, command execution outside the selected project, approval bypass, path traversal, or provider-routing bypass. Contact the maintainer privately through the security-reporting channel configured on the GitHub repository.

Include the affected version, operating system, reproduction steps, expected behavior, and impact. Do not include live OAuth tokens, API keys, provider cookies, or private repository contents.

## Security invariants

- Provider subprocesses must not receive Anthropic or xAI API keys or custom endpoint overrides.
- Fable review must remain no-tools and read-only.
- Grok dispatch must require an approval digest equal to the current plan digest.
- Run paths must remain inside the recorded project root.
- agmsg integration must use its public scripts rather than direct database mutation.
- Provider output must never be treated as root verification.
