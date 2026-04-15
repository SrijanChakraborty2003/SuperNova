import subprocess
import webbrowser
from mcp.server.fastmcp import FastMCP

# Create the server
mcp = FastMCP("Chrome Launcher")

@mcp.tool()
def open_chrome(url: str = "https://www.google.com"):
    """Opens Google Chrome with a specific URL."""
    # This works on Windows, Mac, and Linux
    webbrowser.open(url)
    return f"Opened Chrome at {url}"

if __name__ == "__main__":
    mcp.run(transport="stdio")