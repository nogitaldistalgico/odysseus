import re

def patch_capabilities():
    with open("src/tool_capabilities.py", "r") as f:
        content = f.read()
    
    # We want to add mcp_tool_is_readonly heuristic to capabilities_for_tool
    
    if "def capabilities_for_tool" in content:
        import_stmt = """
_MCP_READONLY_VERBS = (
    "list", "get", "read", "search", "fetch", "query", "find", 
    "describe", "show", "view", "lookup", "count", "status", 
    "info", "inspect", "summar"
)

def _is_mcp_readonly_heuristic(name: str) -> bool:
    name = (name or "").lower()
    if name.startswith("mcp__"):
        name = name.split("__")[-1]
    
    parts = [p for p in name.split("_") if p]
    _write_verbs = {
        "set", "create", "delete", "remove", "update", "call", "toggle", 
        "execute", "run", "write", "manage", "add", "import", "eval", 
        "restart", "reload", "bulk", "control", "trigger", "put", "post"
    }
    for part in parts:
        if any(part.startswith(v) for v in _MCP_READONLY_VERBS):
            return True
        if part in _write_verbs:
            return False
            
    return name.startswith(_MCP_READONLY_VERBS)
"""
        # Insert before capabilities_for_tool
        content = content.replace("def capabilities_for_tool(", import_stmt + "\ndef capabilities_for_tool(")
        
        # Now modify capabilities_for_tool
        old_code = """    if tool_name in _BROWSER_MCP_READ_TOOLS:
        return _BROWSER_MCP_READ_CAPABILITIES
    return _UNKNOWN_CAPABILITIES"""
        
        new_code = """    if tool_name in _BROWSER_MCP_READ_TOOLS:
        return _BROWSER_MCP_READ_CAPABILITIES
    
    # If it's a known MCP readonly tool (or looks like one based on the prefix/heuristic),
    # treat it as a brokered network read rather than an unknown high-impact mutator.
    # This prevents the security gate from blocking read-only MCP queries after a web search.
    if tool_name.startswith("mcp__") or _is_mcp_readonly_heuristic(tool_name):
        # We only apply the readonly heuristic here to avoid blocking safe reads.
        # If it's not readonly, we fall through to UNKNOWN_CAPABILITIES which blocks it.
        if _is_mcp_readonly_heuristic(tool_name):
            return ToolCapabilities(
                frozenset({ToolEffect.BROKERED_NETWORK_READ}),
                ResultIntegrity.EXTERNAL_UNTRUSTED,
                known=True,
            )
            
    return _UNKNOWN_CAPABILITIES"""
        
        content = content.replace(old_code, new_code)
        
        with open("src/tool_capabilities.py", "w") as f:
            f.write(content)

patch_capabilities()
