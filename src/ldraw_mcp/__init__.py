"""ldraw-mcp: render LDraw/LEGO models to images over MCP (headless Blender)."""

from importlib.metadata import version

# Read from installed metadata rather than restated as a literal. The literal and
# pyproject.toml were two hand-maintained copies with nothing enforcing
# agreement — plantcv-mcp shipped 0.2.0 reporting "0.1.0" from exactly that
# arrangement. They agree here; deriving removes the way they stop agreeing.
#
# No PackageNotFoundError fallback, deliberately: a sentinel like
# "0.0.0+unknown" answers a version question with a lie instead of an error.
__version__ = version("ldraw-mcp")
