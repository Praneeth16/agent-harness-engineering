"""A minimal MCP server for the Chapter 5 tools, over stdio.

MCP standardizes how a client lists and calls tools. It does not decide who
may call them: this server takes its principal from the process that launched
it and sends every call through the same Harness.admit the loop uses, so a
tool reached over MCP obeys exactly the checks a tool reached in-process does.

    python -m ahe.mcp_server P042        # one JSON-RPC message per line on stdin

Standard library only. It implements initialize, tools/list, and tools/call,
the subset a client needs to discover and call tools.
"""

import json
import sys

from . import ch05
from .ch02 import request
from .ch03 import start

PROTOCOL = "2026-07-28"
JSON_TYPES = {str: "string", int: "integer", list: "array"}


def schema(tool):
    props = {}
    for p in tool.params:
        props[p.name] = {"type": JSON_TYPES[p.kind], "description": p.doc}
        if p.kind is list:
            props[p.name]["items"] = {"type": "string"}
    return {"type": "object", "properties": props,
            "required": [p.name for p in tool.params], "additionalProperties": False}


class Server:
    def __init__(self, harness, run):
        self.harness, self.run = harness, run

    def handle(self, msg):
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            return {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ahe-screening", "version": "5"}}
        if method == "tools/list":
            tools = [self.harness.tools[n] for n in self.harness.visible(self.run)]
            return {"tools": [{"name": t.name, "description": t.doc,
                               "inputSchema": schema(t)} for t in tools]}
        if method == "tools/call":
            name = params.get("name")
            if name not in self.harness.visible(self.run):   # finish is not a tool
                problem = ch05.refuse(f"no tool {ch05.preview(name)}",
                                      f"tools: {self.harness.visible(self.run)}",
                                      "use one of the listed tools")
                return {"content": [{"type": "text", "text": json.dumps(problem)}], "isError": True}
            proposal = {"tool": name, "args": params.get("arguments", {})}
            try:
                out = self.harness.dispatch(self.run, proposal)
            except Exception as why:            # a tool failed: report it, keep serving
                out = {"error": f"{type(why).__name__}: {why}"}
                self.run.record("observation", **out)
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}],
                    "isError": "error" in out}
        raise KeyError(method)

    def serve(self, lines, write):
        for line in lines:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error"}}) + "\n")
                continue
            if not isinstance(msg, dict) or "id" not in msg:   # notifications need no reply
                continue
            try:
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": self.handle(msg)}
            except KeyError as why:
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601, "message": f"no method {why}"}}
            except Exception as why:
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32603, "message": f"{type(why).__name__}"}}
            write(json.dumps(reply) + "\n")


def main(argv):
    patient = argv[1] if len(argv) > 1 else "P042"
    principal = ch05.REVIEWER_A          # in production: from the authenticated transport
    harness = ch05.Harness(principal, ch05.Submissions())
    run = start(request(patient, 3))
    Server(harness, run).serve(sys.stdin, lambda s: (sys.stdout.write(s), sys.stdout.flush()))


if __name__ == "__main__":
    main(sys.argv)
