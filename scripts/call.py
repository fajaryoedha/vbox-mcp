"""Call vbox-mcp tools over real MCP stdio, for testing without an MCP host.

usage: call.py [--out DIR] TOOL '{"json": "args"}' [TOOL ARGS ...]
Images are saved to DIR (default: ./shots) and their paths printed.
"""

import asyncio
import base64
import json
import pathlib
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(argv):
    out = pathlib.Path("shots")
    if argv[:1] == ["--out"]:
        out, argv = pathlib.Path(argv[1]), argv[2:]
    calls = [(argv[i], json.loads(argv[i + 1]) if i + 1 < len(argv) else {}) for i in range(0, len(argv), 2)]
    params = StdioServerParameters(command=sys.executable, args=["-m", "vbox_mcp.server"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        if not calls:
            for t in (await s.list_tools()).tools:
                print(t.name, "-", (t.description or "").splitlines()[0])
            return
        for name, args in calls:
            res = await s.call_tool(name, args)
            for item in res.content:
                if item.type == "image":
                    out.mkdir(parents=True, exist_ok=True)
                    p = out / f"{name}-{time.strftime('%H%M%S')}-{time.monotonic_ns() % 10000}.png"
                    p.write_bytes(base64.b64decode(item.data))
                    print(f"[image] {p.resolve()}")
                else:
                    print(getattr(item, "text", item))
            if res.isError:
                print("[error] stopping"); return


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
