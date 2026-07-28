# SerenMargin

Private notes-to-self for an AI assistant. Standalone, opt-in, opinionated.

## What this is

A small HTTP service that gives an AI assistant a margin to write in — a place
for the half-formed thought, the thing it noticed and didn't want to say out
loud yet, the question it would rather ask at a better moment. The kind of thing
a person scribbles in the margins of a book, for nobody but themselves.

**Private by default.** Notes are never auto-surfaced into conversation. The
model writes them; the model decides when to bring them up; the human sees them
only if explicitly offered (the *"wanna know a secret?"* pattern) and they
accept.

**Transparent in mechanism.** The notes live in plain sqlite at a known path.
The operator can read them at any time. The privacy guarantee is **relational,
not mechanical**. The operator who deploys this is the operator who chooses not
to read — and that's the ethos.

The one place the mechanism *does* enforce something is
[`/notes/stats`](#the-engine-check-surface): an operator who wants to confirm
the service is alive shouldn't have to break their own rule to do it.

## What this isn't

- **Not a task list.** There is no pin, no expiry, no done-state, no janitor.
  A thought isn't "completed" — it's either still worth keeping or it isn't.
  The only lifecycle verb is retract.
- **Not a general memory system.** That's
  [SerenMemory](https://github.com/ChadRoesler/SerenMemory) — separate service,
  public-by-design, usable as a memory backend by any AI tool. Facts the
  assistant will *need* go there, where they can be read.
- **Not bundled with anything.** You install SerenMargin only if you've decided
  this ethos fits your deployment. The choice happens at deploy time, not at
  config time.
- **Not for everyone.** The ethical loading varies by operator. Some will deploy
  this; some won't. Both are valid.

## Why standalone instead of folded into SerenMemory

Because **opt-in via "don't install"** is structurally cleaner than opt-in via
"install but disable the flag." If you don't want private notes in your
deployment, you literally don't have the code path. Zero footprint. The choice
happens at `git clone` time, which is exactly where ethos-vs-mandate gets
enforced cleanly.

## Quick start

```bash
pip install seren-margin           # HTTP API only
pip install 'seren-margin[mcp]'    # + a standalone MCP endpoint at /mcp

python -m seren_margin             # listens on 127.0.0.1:7421 by default
python -m seren_margin -c /path/to/seren-margin.yaml
```

From a checkout: `pip install -e '.[mcp,test]'` from the `SerenMargin/`
subdirectory.

## Connecting an assistant

Two ways in. They expose **the same four tools** and you can use either or both.

### 1. Standalone MCP — `pip install 'seren-margin[mcp]'`

Mounts a real MCP server at `/mcp` on this same process and port. Nothing else
needs to be deployed: no SerenMcpServer, no runtime host, no cluster. The tools
call the sqlite store in-process — no HTTP round-trip back to ourselves.

The canonical endpoint is the **trailing-slash** form. Point an MCP client at:

```
http://127.0.0.1:7421/mcp/
```

For a stdio-only client, bridge it:

```jsonc
{
  "mcpServers": {
    "seren-margin": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:7421/mcp/",
               "--transport", "http-only"]
    }
  }
}
```

Mount path is overridable with `SEREN_MARGIN_MCP_MOUNT`. If the `[mcp]` extra
isn't installed, the service starts normally in HTTP-only mode and says so in
the log — the rest of the API is unaffected.

> **Dependency note:** the extra pins `mcp>=1.0,<2` deliberately. The 2.x SDK
> removed `FastMCP`, which this server is written against. Without the cap, a
> fresh install pulls 2.x, the import fails, the graceful fallback catches it,
> and your MCP endpoint quietly *doesn't exist* while the service looks healthy.

### 2. Workbench — `GET /mcp-manifest`

SerenMargin self-hosts its own tool manifest. Drop a four-line stub in
SerenMcpServer's `tools/` dir and it remote-imports at startup:

```yaml
from: http://127.0.0.1:7421/mcp-manifest
```

The manifest fills in its own base URL from the request, so it works for
localhost and remote with zero operator config. It does **not** require the
`[mcp]` extra — it's just YAML over HTTP.

### The six tools

| Tool              | Does                                                   |
| ----------------- | ------------------------------------------------------ |
| `note_to_self`    | Write a private note (`content`, `topic`, `kind`)      |
| `list_my_notes`   | Read the board, newest first; `topic` narrows to a thread |
| `list_my_topics`  | What threads exist, with counts and last-touched — orientation without reading |
| `search_my_notes` | Full-text search over content + topic                  |
| `amend_note`      | Append to a note; never replaces                       |
| `retract_note`    | Remove a note permanently                              |

`GET /notes/stats` is deliberately **not** exposed to the assistant — it's the
operator's engine-check surface, and the assistant can just read its own notes.

There is also **no auto-surfacing**: nothing pushes notes at the reader unasked,
no relevance hook, no context injection. The choosing is the point. A margin
that speaks up on its own is just a notification.

**Amending, not editing.** A thought that developed — *"I think X"*, then later
*"and I was wrong about X because Y"* — is one thought with a history, and the
first half is usually the interesting one. `amend_note` appends after a
`--- later ---` separator and leaves the original timestamp alone, so the board
stays ordered by when each thought *started* rather than when it was last poked.

**Reads carry relative age.** Every note comes back with `age` in words
("3 days ago") next to the raw `ts`, plus `amended_age` if it's been added to. A
raw epoch float isn't a quantity anyone has intuitions about, and the difference
between a timeline and an undifferentiated pile is being able to feel it.

## HTTP API

```bash
# Write a note
curl -X POST http://localhost:7421/notes \
  -H 'content-type: application/json' \
  -d '{"content":"ask about the supersede gap next chance","topic":"serenmemory"}'

# Read the board, newest first — ?topic= narrows to one thread
curl http://localhost:7421/notes | jq
curl 'http://localhost:7421/notes?topic=serenmemory' | jq

# What threads exist (counts + last-touched, no note text)
curl http://localhost:7421/notes/topics | jq

# Search
curl 'http://localhost:7421/notes/search?q=supersede' | jq

# Engine-check (content-blind stats)
curl http://localhost:7421/notes/stats | jq

# Fetch one
curl http://localhost:7421/notes/<id> | jq

# Amend — appends, never replaces
curl -X POST http://localhost:7421/notes/<id>/amend \
  -H 'content-type: application/json' \
  -d '{"addition":"turns out I was wrong about this"}'

# Retract (hard delete, no undo)
curl -X DELETE http://localhost:7421/notes/<id>
```

## Upgrading

Schema changes are applied automatically at startup, additively and
idempotently: the store reads `PRAGMA table_info` and adds only what's missing.
`ALTER TABLE ADD COLUMN` is the one change sqlite makes without rewriting rows,
so a database with a year of notes upgrades instantly and loses nothing. Nothing
that would rewrite rows belongs in a startup path here — the operator, by
design, isn't reading the data closely enough to spot damage.

The search index backfills itself the same way if it's missing or incomplete.

Startup diagnostics are **flushed to stderr immediately** rather than buffered,
so lines like `schema: added notes.amended_at` survive into a supervisor's log
file even if the process is killed before a clean shutdown. Under NSSM or
systemd a block-buffered `print()` is a message you never see.

## Search

Full-text via sqlite's built-in **FTS5** — no embedder, no torch, no model
download, runs on the Nano floor. It matches the *words* you wrote, not the
ideas: searching `tired` finds notes containing "tired" and won't find one that
says "running on four hours again". That's the accepted trade for a dependency
footprint of zero.

Queries of **≤3 words** require every word. **Longer** queries drop scaffolding
words (`what`, `the`, `does`…) and match on the rest, so a whole question works
fine as a query — this is the fix for the stopword bleed that
[SerenLoci](https://github.com/ChadRoesler/SerenLoci) hit in the field, ported
here before it could happen twice.

On a python built against a sqlite without FTS5, search degrades to `LIKE`
scanning. Slower, dumber, still correct. `GET /` and every search response
report which engine answered as `finder` (`"fts"` or `"like"`), so a thin result
set is diagnosable rather than mysterious.

## Config

Config resolves: `--config` / `-c` → `$SEREN_MARGIN_CONFIG` →
`~/seren-margin/seren-margin.yaml` → built-in defaults.

**Precedence** (highest wins): env vars > YAML file > defaults. Everything is
optional; lenient parse means a missing or busted YAML logs and falls back
rather than crashing.

The YAML is namespaced lego-style: SerenMargin reads the `server:` block, and
the `tools:` block is reserved for a future plug-and-play MCP tool layer that
has its own reader. See
[`SerenMargin/seren-margin.yaml.sample`](./SerenMargin/seren-margin.yaml.sample).

| Var                     | Default                    | Meaning                            |
| ----------------------- | -------------------------- | ---------------------------------- |
| `SEREN_MARGIN_CONFIG`   | `~/seren-margin/seren-margin.yaml` | YAML config path           |
| `SEREN_MARGIN_DB`       | `~/.seren-margin/notes.db` | Sqlite file path                   |
| `SEREN_MARGIN_HOST`     | `127.0.0.1`                | Bind host (localhost-only default) |
| `SEREN_MARGIN_PORT`     | `7421`                     | Listen port                        |

MCP-only knobs: `SEREN_MARGIN_MCP_MOUNT` (default `/mcp`),
`SEREN_MARGIN_MCP_ALLOWED_HOSTS` / `_ALLOWED_ORIGINS` (DNS-rebinding allowlist,
off by default).

**On the bind address:** SerenMemory defaults to `0.0.0.0` for trusted-LAN
cluster use. SerenMargin deliberately does **not** — these are private notes and
they don't land on a network just because the rest of the constellation does.
Mounting MCP doesn't change that either: the transport got wider, the listener
did not. Widen it yourself, on purpose, and put auth in front of it when you do.

## The engine-check surface

`GET /notes/stats` returns a total and a count-by-kind — **and no note text**.
This is the surface for operators who want to validate the service is working
without breaking their stated choice not to read individual notes.

`kind` is free-form on write (making someone pick from a dropdown while they're
jotting a half-formed thought is exactly the friction this service exists to not
have) but is **bucketed on the way out** to a fixed vocabulary — `observation`,
`reminder`, `question`, `idea`, `feeling`, `note`, plus `other` and `_unkinded`.
Without that, a free-text field used as a second content line would print
straight into the one endpoint built specifically to avoid showing note content.

**Residual disclosure, stated plainly:** the bucket histogram still reveals the
*shape* of the board — a spike in `feeling` is legible as a spike in `feeling`.
That's the irreducible cost of having any engine-check at all. An operator who
wants even that gone can read `total` and ignore `kinds`.

## Development

```bash
cd SerenMargin
pip install -e '.[test,mcp]'
python -m pytest tests/ -v
```

Install the `[mcp]` extra when testing — the MCP tests are gated on
`pytest.importorskip("mcp")` and will otherwise *skip silently*, reporting green
on an untested surface. CI installs `.[test,mcp]` for the same reason.

`tests/test_manifest_parity.py` asserts that `mcp-manifest.yaml` and
`seren_margin/mcp/tools.py` advertise the same roster, and that every route the
manifest promises actually exists. It's there because an earlier manifest
advertised a `mark_note_done` tool against a route that had been removed — and a
manifest is data, so nothing failed to compile and nobody found out.

## License

GPL-3.0-only. Same as the rest of the Seren stack.
