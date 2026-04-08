"""Public page route — serves rendered markdown pages without authentication."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import markdown2

from not_dot_net.backend.page_service import get_page

public_page_router = APIRouter()

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — LPP Intranet</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; padding: 0; color: #333; }}
    .container {{ max-width: 48rem; margin: 0 auto; padding: 2rem; }}
    .back {{ color: #0F52AC; text-decoration: none; font-size: 0.9rem; }}
    .back:hover {{ text-decoration: underline; }}
    h1.title {{ color: #0F52AC; font-weight: 300; margin: 1rem 0 1.5rem; }}
    .content img {{ max-width: 100%; }}
    .content pre {{ background: #f5f5f5; padding: 1rem; overflow-x: auto; border-radius: 4px; }}
    .content code {{ background: #f5f5f5; padding: 0.15em 0.3em; border-radius: 3px; }}
    .content pre code {{ background: none; padding: 0; }}
    .content blockquote {{ border-left: 3px solid #0F52AC; margin-left: 0; padding-left: 1rem; color: #555; }}
    .content table {{ border-collapse: collapse; width: 100%; }}
    .content th, .content td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
    .content th {{ background: #f5f5f5; }}
  </style>
</head>
<body>
  <div class="container">
    <a class="back" href="/">← LPP Intranet</a>
    <h1 class="title">{title}</h1>
    <div class="content">{content}</div>
  </div>
</body>
</html>"""

_NOT_FOUND_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Page not found — LPP Intranet</title>
  <style>
    body {{ font-family: system-ui, sans-serif; display: flex; justify-content: center;
           align-items: center; min-height: 100vh; margin: 0; color: #333; }}
    .center {{ text-align: center; }}
    a {{ color: #0F52AC; }}
  </style>
</head>
<body>
  <div class="center">
    <h1>Page not found</h1>
    <a href="/">← Back to LPP Intranet</a>
  </div>
</body>
</html>"""


@public_page_router.get("/pages/{slug}", response_class=HTMLResponse)
async def public_page(slug: str):
    page = await get_page(slug)
    if page is None or not page.published:
        return HTMLResponse(_NOT_FOUND_TEMPLATE, status_code=404)

    html_content = markdown2.markdown(
        page.content, extras=["fenced-code-blocks", "tables", "strike"],
    )
    return HTMLResponse(
        _PAGE_TEMPLATE.format(title=page.title, content=html_content)
    )
