import sys
import os
import json
import asyncio
from dotenv import load_dotenv
from contextlib import AsyncExitStack
from typing import cast, List, Dict, Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

# Load environment variables
load_dotenv()

class SpotifyAgentClient:
    def __init__(self, api_key: str, max_iterations: int = 5):
        self.openai = AsyncOpenAI(api_key=api_key)
        self.exit_stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.memory: List[ChatCompletionMessageParam] = []
        self.tool_defs: List[Dict[str, Any]] = []
        self.max_iterations = max_iterations

    async def connect(self):
        # Spawn the MCP server via stdio
        cmd = sys.executable
        args = ["-u", "server.py"]
        params = StdioServerParameters(command=cmd, args=args, cwd=os.getcwd())
        read, write = await self.exit_stack.enter_async_context(stdio_client(params))
        self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()

        # List and cache tools
        tools = (await self.session.list_tools()).tools
        print("Connected! Available tools:")
        for t in tools:
            print(f"- {t.name}: {t.description}")
        self.tool_defs = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema}}
            for t in tools
        ]

        # Initialize system prompt in memory
        system_prompt = (
            "You are a reasoning agent with access to Spotify tools. "
            "When needing Spotify actions (searching tracks, controlling playback, managing playlists), decide and call the correct function. "
            "Use a ReAct-style loop: think, act, observe, repeat until you have a final answer."
        )
        self.memory.append({"role": "system", "content": system_prompt})

    async def run_agent(self, user_query: str) -> str:
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        # Add user query to memory
        self.memory.append({"role": "user", "content": user_query})

        for iteration in range(self.max_iterations):
            # Agent thinks and optionally calls a tool
            response = await self.openai.chat.completions.create(
                model="gpt-4o",
                messages=self.memory,
                tools=cast(List[ChatCompletionToolParam], self.tool_defs),
                tool_choice="auto",
            )
            msg = response.choices[0].message

            # If agent chose a tool, execute it
            if msg.tool_calls:
                call = msg.tool_calls[0].function
                func_name = call.name
                args = json.loads(call.arguments or "{}")
                print(f"[Agent] Iteration {iteration+1}: calling tool '{func_name}' with args {args}")
                try:
                    result = await self.session.call_tool(func_name, args)
                    print(f"[Agent] Tool '{func_name}' returned: {result.content}")
                    if hasattr(result, "content"):
                        print(f"[Agent] Tool content:\n{result.content}")
                    if getattr(result, "error", None):
                        print(f"[Agent] Tool error details:\n{result}")
                except Exception:
                    import traceback
                    print("[Agent] Exception during tool call:\n" + traceback.format_exc())
                    raise

                # result = await self.session.call_tool(func_name, args)
                # print(f"[Agent] Tool '{func_name}' returned: {result.content}")

                # Record the function call and its observation
                self.memory.append({"role": "assistant", "name": func_name, "content": json.dumps(args)})
                self.memory.append(cast(
                    ChatCompletionMessageParam,
                    {
                        "role": "function",
                        "name": func_name,
                        "content": result.content,
                    }
                ))
                continue

            # No tool call: final answer
            final_answer = msg.content or ""
            self.memory.append({"role": "assistant", "content": final_answer})
            return final_answer

        # Reached max iterations without final answer
        return "I'm sorry, I couldn't complete the request."

    async def chat_loop(self):
        print("Spotify Agent Chat (type 'quit' to exit)")
        while True:
            query = input("\n> ").strip()
            if not query or query.lower() == "quit":
                break
            try:
                answer = await self.run_agent(query)
                print("\n" + answer)
            except Exception as e:
                print("Error:", e)

    async def close(self):
        await self.exit_stack.aclose()

async def main():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Please set the OPENAI_API_KEY environment variable.")
        sys.exit(1)

    client = SpotifyAgentClient(api_key)
    try:
        await client.connect()
        await client.chat_loop()
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
