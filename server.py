#!/usr/bin/env python3
"""
Wiki.js MCP Server — SSE Transport
Connects Claude to your Wiki.js instance via the GraphQL API.
Runs as a persistent HTTP service (suitable for Docker / Portainer).

Tools:
  search_pages  — Search pages by keyword
  get_page      — Read full content of a page by ID
  list_pages    — List all pages in the wiki
  create_page   — Create a new wiki page
  update_page   — Update an existing wiki page
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
        types.Tool(
            name="create_page",
            description=(
                "Create a new page in Wiki.js. "
                "Requires a title, path, and content. "
                "Returns the new page's ID and path on success."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The page title.",
                    },
                    "path": {
                        "type": "string",
                        "description": "URL path for the page (e.g. 'team/onboarding'). No leading slash.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Page body in Markdown.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description / subtitle shown in listings. Default: empty.",
                        "default": "",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tag strings to attach to the page. Default: [].",
                        "default": [],
                    },
                    "locale": {
                        "type": "string",
                        "description": "Locale code (e.g. 'en'). Default: 'en'.",
                        "default": "en",
                    },
                    "is_published": {
                        "type": "boolean",
                        "description": "Whether to publish the page immediately. Default: true.",
                        "default": True,
                    },
                },
                "required": ["title", "path", "content"],
            },
        ),
        types.Tool(
            name="update_page",
            description=(
                "Update an existing Wiki.js page by its numeric ID. "
                "Only the fields you supply will change; omit fields you want to keep. "
                "Use get_page or search_pages to find the page ID first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "The numeric ID of the page to update.",
                    },
                    "title": {
                        "type": "string",
                        "description": "New page title (leave out to keep existing).",
                    },
                    "content": {
                        "type": "string",
                        "description": "New Markdown body (leave out to keep existing).",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (leave out to keep existing).",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Replacement tag list (leave out to keep existing).",
                    },
                    "is_published": {
                        "type": "boolean",
                        "description": "Published state (leave out to keep existing).",
                    },
                },
                "required": ["id"],
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
        elif name == "create_page":
            return await _create_page(arguments)
        elif name == "update_page":
            return await _update_page(arguments)
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
                authorId
                tags { tag }
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

    tags = ", ".join(t["tag"] for t in (page.get("tags") or []))

    header = (
        f"# {page['title']}\n"
        f"**ID:** {page['id']}  |  **Path:** /{page['path']}\n"
        f"**Updated:** {page.get('updatedAt', '—')}\n"
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


async def _create_page(args: dict) -> list[types.TextContent]:
    mutation = """
    mutation CreatePage(
        $title: String!, $path: String!, $content: String!,
        $description: String!, $tags: [String]!, $locale: String!,
        $isPublished: Boolean!, $isPrivate: Boolean!, $editor: String!
    ) {
        pages {
            create(
                title: $title, path: $path, content: $content,
                description: $description, tags: $tags, locale: $locale,
                isPublished: $isPublished, isPrivate: $isPrivate, editor: $editor
            ) {
                responseResult { succeeded errorCode message }
                page { id path title }
            }
        }
    }
    """
    variables = {
        "title":       args["title"],
        "path":        args["path"].lstrip("/"),
        "content":     args["content"],
        "description": args.get("description", ""),
        "tags":        args.get("tags", []),
        "locale":      args.get("locale", "en"),
        "isPublished": args.get("is_published", True),
        "isPrivate":   False,
        "editor":      "markdown",
    }
    data = await gql(mutation, variables)
    if "errors" in data:
        return [types.TextContent(type="text", text=f"GraphQL error: {data['errors']}")]

    result = data.get("data", {}).get("pages", {}).get("create", {})
    resp   = result.get("responseResult", {})
    if not resp.get("succeeded"):
        return [types.TextContent(
            type="text",
            text=f"Failed to create page: {resp.get('message', 'Unknown error')} (code {resp.get('errorCode')})"
        )]

    page = result.get("page", {})
    return [types.TextContent(
        type="text",
        text=f"✅ Page created successfully!\n**Title:** {page['title']}\n**ID:** {page['id']}\n**Path:** /{page['path']}"
    )]


async def _update_page(args: dict) -> list[types.TextContent]:
    # Fetch existing page first so we only overwrite fields the caller supplied
    fetch_query = """
    query GetPage($id: Int!) {
        pages {
            single(id: $id) {
                id path title description content locale isPublished isPrivate
                tags { tag }
            }
        }
    }
    """
    fetch_data = await gql(fetch_query, {"id": int(args["id"])})
    if "errors" in fetch_data:
        return [types.TextContent(type="text", text=f"GraphQL error fetching page: {fetch_data['errors']}")]

    existing = fetch_data.get("data", {}).get("pages", {}).get("single")
    if not existing:
        return [types.TextContent(type="text", text=f"Page ID {args['id']} not found.")]

    existing_tags = [t["tag"] for t in (existing.get("tags") or [])]

    mutation = """
    mutation UpdatePage(
        $id: Int!, $title: String!, $path: String!, $content: String!,
        $description: String!, $tags: [String]!, $locale: String!,
        $isPublished: Boolean!, $isPrivate: Boolean!, $editor: String!
    ) {
        pages {
            update(
                id: $id, title: $title, path: $path, content: $content,
                description: $description, tags: $tags, locale: $locale,
                isPublished: $isPublished, isPrivate: $isPrivate, editor: $editor
            ) {
                responseResult { succeeded errorCode message }
                page { id path title }
            }
        }
    }
    """
    variables = {
        "id":          int(args["id"]),
        "title":       args.get("title",       existing["title"]),
        "path":        existing["path"],          # path changes require a move; keep as-is
        "content":     args.get("content",     existing["content"]),
        "description": args.get("description", existing.get("description", "")),
        "tags":        args.get("tags",        existing_tags),
        "locale":      existing.get("locale",  "en"),
        "isPublished": args.get("is_published", existing.get("isPublished", True)),
        "isPrivate":   existing.get("isPrivate", False),
        "editor":      "markdown",
    }
    data = await gql(mutation, variables)
    if "errors" in data:
        return [types.TextContent(type="text", text=f"GraphQL error: {data['errors']}")]

    result = data.get("data", {}).get("pages", {}).get("update", {})
    resp   = result.get("responseResult", {})
    if not resp.get("succeeded"):
        return [types.TextContent(
            type="text",
            text=f"Failed to update page: {resp.get('message', 'Unknown error')} (code {resp.get('errorCode')})"
        )]

    page = result.get("page", {})
    return [types.TextContent(
        type="text",
        text=f"✅ Page updated successfully!\n**Title:** {page['title']}\n**ID:** {page['id']}\n**Path:** /{page['path']}"
    )]


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
