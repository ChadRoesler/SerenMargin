# Security Policy

## Supported versions

| Component | Supported |
|-----------|-----------|
| Latest release tag | ✅ |
| Older tags | ❌ |

Security fixes are applied to the current release only. Pin to the latest tag.

---

## What this service holds

An AI assistant's private notes, in plain sqlite at a known path. The privacy
guarantee is **relational, not mechanical**: the operator who deploys this is
the operator who chooses not to read, and the only thing the mechanism
enforces is that the one operator-facing surface, `/notes/stats`, is
content-blind by construction. Everything below is about keeping *other
people* out, not the operator.

Nothing is sent to a third party except the optional update check against the
package index, which `updates.enabled: false` (or
`SEREN_MARGIN_UPDATES_ENABLED=0`) switches off.

---

## Threat model

| Surface | Default | Notes |
|---------|---------|-------|
| HTTP API | `127.0.0.1:7421` | Loopback only. The bind is the guard by default. A host beyond loopback with no token **refuses to start** and prints the three ways out; `allow_open_lan: true` is the written override and prints a banner every boot. |
| Bearer token | Not set | **Optional on loopback, required beyond it.** Set `bearer_token`, `bearer_token_env` or `bearer_token_keyring` on the `server:` block (or the matching `SEREN_MARGIN_*` env vars) and every route but `/`, `/health` and `/mcp-manifest` wants `Authorization: Bearer <token>`. Nothing makes you set one to keep a diary on your own machine. |
| MCP endpoint (`/mcp`) | Only with the `[mcp]` extra, same host/port | Behind the same optional bearer. Six tools, all note-scoped; no tool reads `/notes/stats`. DNS-rebinding protection is off by default for a trusted LAN; `SEREN_MARGIN_MCP_ALLOWED_HOSTS` turns it on. |
| Tool manifest (`/mcp-manifest`) | Public | Tool descriptions and this instance's base URL, never notes. Public so Workbench can import it before it has credentials. |
| The database | `~/.seren-margin/notes.db`, mode of the user's umask | Readable by the deploying user. That is the arrangement; back it up like anything else you would not want to lose, and do not put it on a shared filesystem. |
| Config file | `~/seren-margin/seren-margin.yaml` | May hold an inline `bearer_token`. Prefer the env-var or keyring pointer; keep the file `0600` and out of version control. |
| Retract | Hard delete, no history | `DELETE /notes/{id}` and the `retract_note` tool remove a note for good. There is deliberately no archive of retracted notes. |

---

## Deployment recommendations

- **One box, one assistant** (the intended case): the defaults. Loopback, no
  token, install it only if the ethos fits your deployment.
- **Reachable from another machine**: set a token first, then widen the
  bind, then decide whether a reverse proxy or an SSH tunnel is the better
  door. The service will not start the other way round.
- **Anything routable from outside the house**: don't.

---

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Open a [GitHub Security Advisory](https://github.com/ChadRoesler/SerenMargin/security/advisories/new) (private disclosure). Include:

- A description of the issue and its impact
- Steps to reproduce
- Any relevant config or environment details

You will get a response within **7 days**. If a fix is needed, a patched release will be tagged and the advisory will be published after users have had time to update.
