import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent

PARAMS = StdioServerParameters(
    command=sys.executable,
    args=[str(ROOT / "server.py")],
    env={"GETJOBS_BASE_URL": os.environ.get("GETJOBS_BASE_URL", "http://localhost:9527")},
)


async def main() -> None:
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", sorted(t.name for t in tools.tools))
            result = await session.call_tool("get_boss_status", {})
            print("STATUS:", result.content[0].text[:300])
            result = await session.call_tool(
                "validate_plan",
                {"plan": {"keywords": ["大模型应用开发"], "city": "上海", "salary": ["20-30K"]}},
            )
            print("VALIDATE:", result.content[0].text[:400])


if __name__ == "__main__":
    asyncio.run(main())