"""CLI-Anything harness for SOLIDWORKS via its MCP server."""

from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-solidworks",
    version="0.1.0",
    description="CLI-Anything harness for SOLIDWORKS 2025 via MCP",
    author="panyiguang060821",
    url="https://github.com/panyiguang060821/soildworks-mcp-opencode-",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    package_data={"cli_anything.solidworks": ["skills/*.md"]},
    include_package_data=True,
    install_requires=[
        "click>=8.0",
        "mcp>=1.0",
        "colorama>=0.4",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-solidworks=cli_anything.solidworks.solidworks_cli:cli",
        ],
    },
    python_requires=">=3.10",
)
