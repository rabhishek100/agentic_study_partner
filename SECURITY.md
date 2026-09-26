# Security

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed secret.
Instead, email the maintainer privately at the address on their GitHub profile
([@rabhishek100](https://github.com/rabhishek100)), with steps to reproduce and
the affected commit.

## Supported version

Only the latest commit on `main` is maintained. There are no released or
supported version lines yet.

## Security boundaries

- Browser requests require verified Supabase access tokens.
- User identity comes from the token, never from a request-supplied owner ID.
- Source files and media are private; access is authenticated or short-lived.
- Service-role, database, model-provider, storage, and LiveKit credentials stay
  server-side.
- Upload limits, content checks, owner-scoped rows/keys, and worker leases are
  enforced server-side.
- Model output is treated as data. The application never executes unrestricted
  model-generated SQL or shell commands.
- Raw interview audio and screen checkpoints are not stored.

Security controls in this repository have automated coverage, but the project
has not had an independent security audit or penetration test.
