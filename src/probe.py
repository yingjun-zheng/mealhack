"""MCP 能力探测：先把 33 个 tool 的真实 schema 抓下来，再决定怎么调。

运行：python src/probe.py
产物：samples/tools.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_client import McdMCP, McpError  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "samples"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cli = McdMCP()

    print("=" * 70)
    print("initialize")
    print("=" * 70)
    info = cli.initialize()
    print(json.dumps(info, ensure_ascii=False, indent=2)[:800])

    print()
    print("=" * 70)
    print("tools/list")
    print("=" * 70)
    tools = cli.list_tools()
    (OUT / "tools.json").write_text(
        json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"共 {len(tools)} 个 tool，已存入 samples/tools.json\n")

    for tool in tools:
        name = tool.get("name", "?")
        schema = tool.get("inputSchema") or {}
        required = schema.get("required") or []
        props = schema.get("properties") or {}
        desc = (tool.get("description") or "").replace("\n", " ").strip()

        print(f"[{name}]")
        print(f"  必填: {required or '无'}")
        for key, spec in props.items():
            typ = spec.get("type", "?")
            enum = spec.get("enum")
            enum_txt = f" enum={enum}" if enum else ""
            flag = "*" if key in required else " "
            print(f"   {flag} {key}: {typ}{enum_txt}")
        print(f"  说明: {desc[:160]}")
        print()

    print(f"累计请求数: {cli.calls}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except McpError as exc:
        print(f"\n[失败] {exc}")
        raise SystemExit(1) from None
