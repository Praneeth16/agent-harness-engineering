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
            proposal = {"tool": params.get("name"), "args": params.get("arguments", {})}
            self.run.record("proposal", proposal=proposal)
            problem = self.harness.admit(self.run, proposal)
            if problem:
                self.run.record("gate", refused=problem)
                return {"content": [{"type": "text", "text": json.dumps(problem)}],
                        "isError": True}
            out = self.harness.tools[proposal["tool"]].fn(self.run, **proposal["args"])
            self.run.record("observation", tool=proposal["tool"], **out)
            return {"content": [{"type": "text", "text": json.dumps(out, default=str)}],
                    "isError": "error" in out}
        raise KeyError(method)

    def serve(self, lines, write):
        for line in lines:
            if not line.strip():
                continue
            msg = json.loads(line)
            if "id" not in msg:          # a notification needs no reply
                continue
            try:
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": self.handle(msg)}
            except KeyError as why:
                reply = {"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601, "message": f"no method {why}"}}
            write(json.dumps(reply) + "\n")


def main(argv):
    patient = argv[1] if len(argv) > 1 else "P042"
    principal = ch05.REVIEWER_A          # in production: from the authenticated transport
    harness = ch05.Harness(principal, ch05.Submissions())
    run = start(request(patient, 3))
    Server(harness, run).serve(sys.stdin, lambda s: (sys.stdout.write(s), sys.stdout.flush()))


if __name__ == "__main__":
    main(sys.argv)
