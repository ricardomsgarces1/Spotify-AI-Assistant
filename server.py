import logging
import sys

# ——————————————————————————————————————————————————————————————
# 0. ensure we log full stack traces to stdout
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s"
)
# ——————————————————————————————————————————————————————————————

import os
import sys
import yaml
import re

from openapi_spec_validator import validate_spec
from fastmcp import FastMCP
from utils.auth import get_oauth_session

# 1. Load and validate the OpenAPI spec
encoding = os.getenv("SPEC_ENCODING", "utf-8")
with open("spotify-openapi.yaml", encoding=encoding) as f:
    spec = yaml.safe_load(f)
validate_spec(spec)

# 2. Prepare OAuth2 session (auto-refresh) and HTTP client
oauth = get_oauth_session()
base_url = spec["servers"][0]["url"].rstrip("/")

# 3. Instantiate FastMCP server instance
mcp = FastMCP("Spotify MCP")

# 4. Resolve $ref to real parameter definitions
def resolve_param(param):
    if "$ref" in param:
        # Follow JSON Reference to components/parameters
        parts = param["$ref"].lstrip("#/").split("/")
        obj = spec
        for key in parts:
            obj = obj[key]
        return obj
    return param

# 5. Sanitize raw names into valid Python identifiers
def sanitize_name(name: str) -> str:
    # replace non-alphanumeric/underscore with underscore
    sanitized = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    # prefix leading digits
    if re.match(r"^\d", sanitized):
        sanitized = f"op_{sanitized}"
    return sanitized

# 6. Factory: create async tool functions with explicit args
def make_tool_function(tool_name: str,
                       path: str,
                       method: str,
                       param_objs: list,
                       op: dict):
    """
    param_objs: list of dicts { 'name': <paramName>, 'in': 'path'|'query' }
    op: the OpenAPI OperationObject (so we can see requestBody)
    """
    # split params
    path_params  = [p["name"] for p in param_objs if p["in"] == "path"]
    query_params = [p["name"] for p in param_objs if p["in"] == "query"]

    # do we need a JSON body?
    has_body = "requestBody" in op
    all_args = path_params + query_params + (["body"] if has_body else [])

    # signature
    sig = ", ".join(all_args)
    params_literal = ", ".join(f"'{p}': {p}" for p in query_params)

    lines = []
    lines.append(f"async def {tool_name}({sig}):")
    # construct the URL
    lines.append(f"    url = f\"{base_url}{path}\"")
    lines.append("    try:")
    # make the request call
    call_args = []
    if query_params:
        call_args.append(f"params={{ {params_literal} }}")
    if has_body:
        call_args.append("json=body")
    call_args = ", " + ", ".join(call_args) if call_args else ""
    lines.append(f"        resp = oauth.request({method!r}, url{call_args})")
    lines.append("        resp.raise_for_status()")
    lines.append("        # handle empty-body / 204 / 201")
    lines.append("        if resp.status_code == 201 or resp.status_code == 204 or not resp.text:")
    lines.append("            return {'isError': False, 'content': [\"Sucessfully executed\"]}")
    lines.append("        return {")
    lines.append("            'isError': False,")
    lines.append("            'content': resp.json()")
    lines.append("        }")
    lines.append("    except Exception as e:")
    lines.append("        # log full traceback + HTTP detail")
    lines.append(f"        logging.exception('[{tool_name}] HTTP call failed', exc_info=True)")
    lines.append("        # if this was an HTTPError we can pull out status & body")
    lines.append("        status = getattr(e, 'response', None) and e.response.status_code")
    lines.append("        body   = getattr(e, 'response', None) and e.response.text")
    lines.append("        error_text = f\"HTTP {status}: {body}\" if status else str(e)")
    lines.append("        return {")
    lines.append("            'isError': True,")
    lines.append("            'content': [")
    lines.append("                { 'type': 'text', 'text': error_text }")
    lines.append("            ]")
    lines.append("        }")

    # join & exec
    fn_src = "\n".join(lines)

    # print(fn_src)
    # print("\n\n")
    namespace = { "oauth": oauth, "base_url": base_url, "logging": logging }
    exec(fn_src, namespace)
    return namespace[tool_name]


# Allowed HTTP methods
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}

# 7. Iterate through all paths and register as tools
for path, methods in spec.get("paths", {}).items():
    for method, op in methods.items():
        if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
            continue

        raw_name = op.get("operationId") or f"{method}_{path.strip('/').replace('/', '_')}"
        tool_name = sanitize_name(raw_name)

        # collect resolved params
        param_objs = []
        for p in op.get("parameters", []):
            p_obj = resolve_param(p)
            if p_obj.get("in") in ("path", "query"):
                param_objs.append({ "name": p_obj["name"], "in": p_obj["in"] })

        # make & register
        fn = make_tool_function(tool_name, path, method.upper(), param_objs, op)
        mcp.add_tool(fn, name=tool_name, description=op.get("summary", ""))


if __name__ == "__main__":
    # STDIO transport (default):
    mcp.run()

    # Streamable HTTP transport (recommended for web clients):
    # mcp.run(transport="streamable-http", host="127.0.0.1", port=8000)
