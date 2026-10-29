"""Restrict blind reviewers to the built-in artifact readers."""

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.tools.list_files import ListFiles
from crux_scaffold.tools.read_file import ReadFile
from crux_scaffold.tools.read_pdf import ReadPdf
from crux_scaffold.tools.tool import Tool


def validate_review_tools(tools: list[Tool]) -> None:
    if any(type(tool) not in (ReadFile, ListFiles, ReadPdf) for tool in tools):
        raise InvalidDropInError("blind review tools must be read_file, list_files or read_pdf implementations")
