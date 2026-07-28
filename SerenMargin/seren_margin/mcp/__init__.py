"""
seren_margin.mcp
════════════════

Optional MCP server surface for SerenMargin. Only meaningful when the [mcp]
extras are installed (`pip install seren-margin[mcp]`); without those deps this
subpackage's modules fail to import and app.py's mount-attempt silently no-ops,
leaving SerenMargin in pure-HTTP mode.

WHY THIS EXISTS SEPARATELY FROM /mcp-manifest:
    SerenMargin already self-hosts a manifest at GET /mcp-manifest, which lets
    SerenMcpServer remote-import these tools when the full workbench is running.
    That path is unchanged and still correct.

    This subpackage is the OTHER path: a standalone MCP endpoint on this same
    process, so a model can reach its own margin with nothing else deployed -
    no runtime host, no cluster, no C# in the loop. Just `pip install
    seren-margin[mcp]`, point a client at /mcp, done. Same reason SerenMemory
    and SerenLoci each grew one.

    Both surfaces expose the same four tools. They're defined ONCE, in tools.py;
    the manifest is written to match and both are asserted against each other in
    tests/test_manifest_parity.py, because the previous manifest drifted from the
    routes and shipped a tool that 404'd for who knows how long.

The tools call MarginStore directly (not via an HTTP round-trip to ourselves)
since we're mounted INTO the same FastAPI app that owns the store. Less wire,
less latency, fewer failure modes.
"""
