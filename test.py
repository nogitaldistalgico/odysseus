import sys
import os
sys.path.insert(0, os.path.abspath("src"))
from mcp_manager import mcp_tool_is_readonly
tool = {"name": "ha_search"}
print(f"ha_search: {mcp_tool_is_readonly(tool)}")
tool2 = {"name": "ha_config_get_automation"}
print(f"ha_config_get_automation: {mcp_tool_is_readonly(tool2)}")
