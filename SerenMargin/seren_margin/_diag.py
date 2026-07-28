"""Diagnostic output for SerenMargin.

WHY THIS EXISTS INSTEAD OF bare print():

Python block-buffers stdout when it isn't a terminal. Under a service
supervisor - NSSM on Windows, systemd on Linux, anything redirecting to a log
file - that means a `print()` sits in a 8KB buffer until it fills or the process
exits cleanly. A service that gets killed, restarted, or crashes loses whatever
was still in the buffer.

The lines this service prints are exactly the ones you can't afford to lose:

    [seren-margin] schema: added notes.amended_at
    [seren-margin] rebuilt search index (412 notes)
    [seren-margin] sqlite has no FTS5; search will use the LIKE fallback
    [seren-margin] config: ignored bad value for 'port'

Every one of those is a "something changed under you at startup" message. Losing
the migration line after an upgrade, on a service whose whole design premise is
that its operator ISN'T reading the data closely enough to spot damage, is the
worst possible thing to drop on the floor.

So: flush on every write. This is a handful of lines a day at most - there is no
throughput argument on the other side.

Deliberately not the `logging` module. SerenMargin's output is a short list of
startup facts an operator reads once, not an event stream anyone filters, and a
logger would need configuring by every embedder to show them at all. Sinew
handles real request logging for the family; this is just startup narration that
reliably lands.
"""
from __future__ import annotations

import sys


def diag(message: str) -> None:
    """Print a startup/diagnostic line and flush it immediately.

    Writes to stderr: it's unbuffered-by-default in the C sense, supervisors
    capture it alongside stdout, and it keeps diagnostics out of anything
    parsing stdout.
    """
    print(message, file=sys.stderr, flush=True)
