"""Safe Markdown rendering for post bodies.

Raw HTML in the source is escaped (not interpreted), headings are
shifted so ``#`` becomes <h3>, and the output is sanitised with bleach.
"""
from xml.etree.ElementTree import Element

import bleach
import markdown as md
from bleach.callbacks import nofollow
from markdown import Markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from markupsafe import Markup

ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "del",
    "ul", "ol", "li", "a", "h3", "h4", "h5", "h6",
    "code", "pre", "blockquote",
})
ALLOWED_ATTRIBUTES = {"a": ["href", "title", "rel"]}
ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})


class _ShiftHeadings(Treeprocessor):
    """h1 -> h3, h2 -> h4, ... capped at h6 (page already has h1/h2)."""

    def run(self, root: Element) -> None:
        """Shift every heading in the tree down two levels.

        Args:
            root: The root element of the parsed document.
        """
        for el in root.iter():
            if el.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                el.tag = f"h{min(int(el.tag[1]) + 2, 6)}"


class _FamilySafeExtension(Extension):
    """Markdown extension that escapes raw HTML and shifts headings."""

    def extendMarkdown(self, mdi: Markdown) -> None:  # noqa: N802
        """Switch off raw HTML and add the heading shifter.

        The name is dictated by Markdown's ``Extension`` API.

        Args:
            mdi: The Markdown instance being configured.
        """
        # Treat raw HTML as plain text so it is escaped, not rendered.
        mdi.preprocessors.deregister("html_block")
        mdi.inlinePatterns.deregister("html")
        mdi.treeprocessors.register(_ShiftHeadings(mdi), "shift_headings", 5)


def render_markdown(text: str | None) -> Markup:
    """Render Markdown to sanitised HTML safe to put in a page.

    Args:
        text: The Markdown source, or ``None`` for an empty body.

    Returns:
        The cleaned HTML, with bare URLs linked as ``nofollow``.
    """
    html = md.markdown(
        text or "",
        extensions=[
            "sane_lists", "nl2br", "fenced_code", _FamilySafeExtension(),
        ],
        output_format="html",
    )
    html = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES,
                        protocols=ALLOWED_PROTOCOLS, strip=True)
    # Turn bare URLs into links and add rel="nofollow" to every link.
    html = bleach.linkify(
        html, callbacks=[nofollow], skip_tags={"pre", "code"})
    return Markup(html)
