# SerenMargin

Private notes-to-self for an AI assistant. Standalone, opt-in, opinionated.

## What this is

A small HTTP service that lets an AI assistant maintain a corkboard of private
notes-to-self - reminders, observations, *"ask Chad about X next chance I get"*
notes. The kind of thing a human would scribble in the margins of a book.

**Private by default.** Notes are never auto-surfaced into conversation. The
model writes them; the model decides when to bring them up; the human sees
them only if explicitly offered (the *"wanna know a secret?"* pattern) and
they accept.

**Transparent in mechanism.** The notes live in plain sqlite at a known path.
The operator can read them at any time. The privacy guarantee is **relational,
not mechanical**. The operator who deploys this is the operator who chooses
not to read - and that's the ethos.

## What this isn't

- **Not a general memory system.** That's
  [SerenMemory](https://github.com/ChadRoesler/SerenMemory) - separate
  service, public-by-design, usable as a memory backend by any AI tool.
- **Not bundled with anything.** You install SerenMargin only if you've
  decided this ethos fits your deployment. The choice happens at deploy time,
  not at config time.
- **Not for everyone.** The ethical loading varies by operator. Some will
  deploy this; some won't. Both are valid.

## Why standalone instead of folded into SerenMemory

Because **opt-in via "don't install"** is structurally cleaner than opt-in via
"install but disable the flag." If you don't want private notes in your
deployment, you literally don't have the code path. Zero footprint. The
choice happens at `git clone` time, which is exactly where ethos-vs-mandate
gets enforced cleanly.

## Quick start

**Friendly path (per-user venv + systemd-ready):**

```bash
./install.sh        # installs into ~/.seren-margin/venv, drops samples
                    # see install.sh --help for flags
~/.seren-margin/venv/bin/seren-margin   # run it directly to smoke test
```

**Manual path:**

```bash
pip install -e .
python -m seren_margin     # listens on 127.0.0.1:7421 by default
```

**Interacting with it:**

```bash
# Write a note
curl -X POST http://localhost:7421/notes \
  -H 'content-type: application/json' \
  -d '{"content":"ask Chad about the supersede gap next chance"}'

# List active notes (corkboard view)
curl http://localhost:7421/notes | jq

# Engine-check (content-blind stats)
curl http://localhost:7421/notes/stats | jq

# Mark done (corkboard "pulled")
curl -X POST http://localhost:7421/notes/<id>/done

# Pin (exempt from aging)
curl -X POST http://localhost:7421/notes/<id>/pin

# Hard delete
curl -X DELETE http://localhost:7421/notes/<id>
```

## Config

Lego framing: the config YAML is split into namespaced sections. SerenMargin
itself reads the `service:` block; the `tools:` block is reserved for a future
plug-and-play MCP tool layer (when `note_to_self` lands, ITS config lives
there, and SerenMargin still won't read that block).

**Precedence** (highest wins): env vars > YAML file > built-in defaults.
Everything is optional; lenient parse means a missing or busted YAML
silently falls back to defaults.

**YAML location:** `~/.config/seren-margin/config.yaml` by default.
Override with `SEREN_MARGIN_CONFIG=/path/to/config.yaml`.

See [`SerenMargin/seren-margin.yaml.sample`](./SerenMargin/seren-margin.yaml.sample)
for the full annotated example.

Env vars (all optional; useful for systemd `Environment=` overrides):

| Var                       | Default                       | Meaning                            |
| ------------------------- | ----------------------------- | ---------------------------------- |
| `SEREN_MARGIN_CONFIG`     | `~/.config/seren-margin/config.yaml` | YAML config path           |
| `SEREN_MARGIN_DB`         | `~/.seren-margin/notes.db`    | Sqlite file path                   |
| `SEREN_MARGIN_HOST`       | `127.0.0.1`                   | Bind host (localhost-only default) |
| `SEREN_MARGIN_PORT`       | `7421`                        | Listen port                        |
| `SEREN_MARGIN_NOTES_DAYS` | `30`                          | Auto-expire active notes after N days unless pinned |

## The engine-check surface

`GET /notes/stats` returns counts + shape + age stats - **but no content**.
This is the surface for operators who want to validate the service is working
without breaking their stated relational choice not to read individual notes.

Counts by `kind` are included, since kinds are operator-facing taxonomy rather
than note text. If even kind distribution feels too revealing for your
deployment, that's a flag worth adding (PR welcome).

## License

GPL-3.0-only. Same as the rest of the Seren stack.

## Repo layout

```
SerenMargin/                              ← repo root
├── LICENSE                               ← GPL-3.0-only
├── README.md                             ← you are here
├── install.sh                            ← per-user venv + samples drop
├── pyproject.toml                        ← outer build config
├── SerenMargin.slnx                      ← VS solution wrapper
├── .github/workflows/release.yml         ← CI: test + tag-triggered release
└── SerenMargin/                          ← VS project dir + package home
    ├── pyproject.toml                    ← inner build config (used by CI)
    ├── SerenMargin.pyproj                ← VS Python project file
    ├── seren-margin.yaml.sample          ← annotated YAML config sample
    ├── seren-margin.service.sample       ← systemd user-unit sample
    ├── seren_margin/                     ← THE PACKAGE
    │   ├── __init__.py
    │   ├── __main__.py                   ← entry point: python -m seren_margin
    │   ├── app.py                        ← FastAPI app + endpoints
    │   ├── config.py                     ← YAML + env loader (lenient parse)
    │   ├── models.py                     ← MarginNote, NoteCreate, NoteStats
    │   └── store.py                      ← sqlite-backed store
    └── tests/
        ├── test_smoke.py                 ← write/list/done/delete cycle + content-blind stats
        └── test_config.py                ← YAML precedence + lenient parse
```
