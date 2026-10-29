from __future__ import annotations

from contextlib import closing
from io import BytesIO

import pypdfium2 as pdfium

from crux_scaffold.errors import ToolError
from crux_scaffold.tools.pdf_arguments import PdfArguments
from crux_scaffold.tools.registry import TOOLS
from crux_scaffold.tools.tool import Tool
from crux_scaffold.tools.tool_image import ToolImage
from crux_scaffold.tools.tool_output import ToolOutput
from crux_scaffold.workspace import MAX_OUTPUT_CHARS, RunContext


@TOOLS.register
class ReadPdf(Tool):
    """Return bounded text and a rendered image from one workspace PDF page."""

    type_name = "read_pdf"
    description = "Read one PDF page as extracted text and an image, including figures. Reports the page count."
    Arguments = PdfArguments

    async def invoke(self, ctx: RunContext, args: PdfArguments) -> ToolOutput:
        content = ctx.workspace.read_bytes(args.path)
        try:
            with pdfium.PdfDocument(content) as document:
                if args.page > len(document):
                    raise ToolError(f"PDF page must be between 1 and {len(document)}")
                with closing(document[args.page - 1]) as page:
                    with closing(page.get_textpage()) as textpage:
                        text = textpage.get_text_bounded()[:MAX_OUTPUT_CHARS]
                    width, height = page.get_size()
                    with (
                        closing(page.render(scale=min(2, 1600 / max(width, height)))) as bitmap,
                        bitmap.to_pil() as image,
                    ):
                        buffer = BytesIO()
                        image.save(buffer, format="PNG")
                    return [f"Page {args.page} of {len(document)}\n{text}", ToolImage(buffer.getvalue())]
        except (pdfium.PdfiumError, ValueError, OSError) as exc:
            if isinstance(exc, ToolError):
                raise
            raise ToolError("cannot read PDF page") from None
