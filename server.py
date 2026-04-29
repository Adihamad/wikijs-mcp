#!/usr/bin/env python3
"""
Wiki.js MCP Server — SSE Transport
Connects Claude to your Wiki.js instance via the GraphQL API.
Runs as a persistent HTTP service (suitable for Docker / Portainer).

Tools:
  search_pages  — Search pages by keyword
  get_page      — Read full content of a page by ID
  list_pages    — List all pages in the wiki
"""

import asyncio
import os

import httpx
import uvicorn
import mcp.types as types
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route

# ─── Configuration ─────────────────────────────────────────────────────────────
WIKI_URL     = os.environ.get("WIKIJS_URL", "https://mywiki.aditnas.org").rstrip("/")
WIKI_API_KEY = os.environ.get("WIKIJS_API_KEY", "")
GRAPHQL_URL  = f"{WIKI_URL}/graphql"
MCP_PORT     = int(os.environ.get("MCP_PORT", "3001"))
MCP_HOST     = os.environ.get("MCP_HOST", "0.0.0.0")
# ───────────────────────────────────────────────────────────────────────────────

app = Server("wikijs-mcp")


async def gql(query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query against the Wiki.js API."""
    if not WIKI_API_KEY:
        raise RuntimeError(
            "WIKIJS_API_KEY is not set. "
            "Generate one in Wiki.js → Administration → API Access."
        )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {WIKI_API_KEY}",
    }
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(GRAPHQL_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


# ─── Tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_pages",
            description=(
                "Search for pages in Wiki.js by keyword. "
                "Returns matching page titles, IDs, paths, and descriptions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search term or phrase to look for.",
                    }
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_page",
            description=(
                "Read the full Markdown content of a Wiki.js page by its numeric ID. "
                "Use search_pages or list_pages first to find the ID."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "The numeric page ID (e.g. 42).",
                    }
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="list_pages",
            description=(
                "List pages in Wiki.js. Returns ID, path, title, and description."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of pages to return. Default: 50.",
                        "default": 50,
                    },
                    "order_by": {
                        "type": "string",
                        "description": "Sort order: TITLE, PATH, CREATED, or UPDATED. Default: TITLE.",
                        "enum": ["TITLE", "PATH", "CREATED", "UPDATED"],
                        "default": "TITLE",
                    },
                },
            },
        ),
    ]


# ─── Tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "search_pages":
            return await _search_pages(arguments)
        elif name == "get_page":
            return await _get_page(arguments)
        elif name == "list_pages":
            return await _list_pages(arguments)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except httpx.HTTPStatusError as e:
        return [types.TextContent(type="text", text=f"HTTP error {e.response.status_code}: {e.response.text}")]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]


async def _search_pages(args: dict) -> list[types.TextContent]:
    query = """
    query SearchPages($query: String!) {
        pages {
            search(query: $query) {
                results { id path title description locale }
                totalHits
            }
        }
    }
    """
    data = await gql(query, {"query": args["query"]})
    if "errors" in data:
        return [types.TextContent(type="text", text=f"GraphQL error: {data['errors']}")]

    search  = data.get("data", {}).get("pages", {}).get("search", {})
    results = search.get("results", [])
    total   = search.get("totalHits", 0)

    if not results:
        return [types.TextContent(type="text", text="No pages matched your search.")]

    lines = [f"Found {total} result(s) for '{args['query']}':\n"]
    for p in results:
        lines.append(f"• [{p['title']}] (ID: {p['id']})  path: /{p['path']}")
        if p.get("description"):
            lines.append(f"  {p['description']}")
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_page(args: dict) -> list[types.TextContent]:
    query = """
    query GetPage($id: Int!) {
        pages {
            single(id: $id) {
                id path title description content
                updatedAt createdAt
                tags { tag }
                author { name }
            }
        }
    }
    """
    data = await gql(query, {"id": int(args["id"])})
    if "errors" in data:
        return [types.TextContent(type="text", text=f"GraphQL error: {data['errors']}")]

    page = data.get("data", {}).get("pages", {}).get("single")
    if not page:
        return [types.TextContent(type="text", text="Page not found. Double-check the ID.")]

    tags   = ", ".join(t["tag"] for t in (page.get("tags") or []))
    author = (page.get("author") or {}).get("name", "Unknown")

    header = (
        f"# {page['title']}\n"
        f"**ID:** {page['id']}  |  **Path:** /{page['path']}\n"
        f"**Author:** {author}  |  **Updated:** {page.get('updatedAt', '—')}\n"
    )
    if tags:
        header += f"**Tags:** {tags}\n"
    if page.get("description"):
        header += f"**Description:** {page['description']}\n"
    header += "\n---\n\n"

    return [types.TextContent(type="text", text=header + (page.get("content") or "*(no content)*"))]


async def _list_pages(args: dict) -> list[types.TextContent]:
    limit    = int(args.get("limit", 50))
    order_by = args.get("order_by", "TITLE")

    query = """
    query ListPages($limit: Int!, $orderBy: PageOrderBy!) {
        pages {
            list(limit: $limit, orderBy: $orderBy) {
                id path title description updatedAt
            }
        }
    }
    """
    data = await gql(query, {"limit": limit, "orderBy": order_by})
    if "errors" in data:
        return [types.TextContent(type="text", text=f"GraphQL error: {data['errors']}")]

    pages = data.get("data", {}).get("pages", {}).get("list", [])
    if not pages:
        return [types.TextContent(type="text", text="No pages found.")]

    lines = [f"Listing {len(pages)} pages (ordered by {order_by}):\n"]
    for p in pages:
        lines.append(f"• [ID {p['id']}]  {p['title']}  —  /{p['path']}")
        if p.get("description"):
            lines.append(f"  {p['description']}")
    return [types.TextContent(type="text", text="\n".join(lines))]


# ─── SSE Server setup ──────────────────────────────────────────────────────────

def build_starlette_app(mcp_server: Server) -> Starlette:
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0], streams[1], mcp_server.create_initialization_options()
            )

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )


if __name__ == "__main__":
    print(f"Starting Wiki.js MCP server on {MCP_HOST}:{MCP_PORT}")
    print(f"Wiki.js endpoint: {WIKI_URL}")
    starlette_app = build_starlette_app(app)
    uvicorn.run(starlette_app, host=MCP_HOST, port=MCP_PORT)
