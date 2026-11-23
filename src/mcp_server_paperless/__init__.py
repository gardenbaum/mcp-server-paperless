"""MCP server for paperless-ngx 3.x."""

import importlib.metadata

# The distribution is mcp-server-paperless on PyPI; paperless-mcp there is another project.
__version__ = importlib.metadata.version("mcp-server-paperless")
