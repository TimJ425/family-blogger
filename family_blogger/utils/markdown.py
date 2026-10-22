"""Safe Markdown rendering for post bodies.

Raw HTML in the source is escaped (not interpreted), headings are
shifted so ``#`` becomes <h3>, bare web addresses become links, and
the output is sanitised with nh3 against a small allowlist.
"""
import re
from xml.etree.ElementTree import Element

import markdown as md
import nh3
from markdown import Markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from markupsafe import Markup

ALLOWED_TAGS = frozenset({
    "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "del",
    "ul", "ol", "li", "a", "h3", "h4", "h5", "h6",
    "code", "pre", "blockquote",
})
# nh3 sets ``rel`` on every link itself (see LINK_REL), so ``rel``
# must not be in the attribute allowlist.
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}
ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})
LINK_REL = "nofollow noopener noreferrer"

# C0 control characters except tab, newline and carriage return.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# A placeholder Python-Markdown failed to put back.
_PLACEHOLDER_RE = re.compile(r"\x02\d*\x03")

# Bare web addresses to turn into links. \x02 and \x03 mark
# Markdown's internal placeholders, which must never be split.
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'`\x02\x03]+",
                     re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}'\""
# Never add links inside these elements.
_NO_LINK_TAGS = {"a", "code", "pre"}


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


def _split_urls(text: str) -> list[str | Element]:
    """Split text into plain pieces and ``<a>`` elements for URLs.

    Trailing punctuation (a full stop ending a sentence, a closing
    bracket) stays outside the link. ``www.`` addresses get
    ``http://`` in front.

    Args:
        text: Plain text from the parsed document.

    Returns:
        Strings and link elements in their original order.
    """
    pieces = []
    pos = 0
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        if not url or url.lower() in ("http://", "https://", "www."):
            continue
        pieces.append(text[pos:match.start()])
        link = Element("a")
        if url.lower().startswith("www."):
            link.set("href", "http://" + url)
        else:
            link.set("href", url)
        link.text = url
        pieces.append(link)
        pos = match.start() + len(url)
    pieces.append(text[pos:])
    return pieces


class _Linkify(Treeprocessor):
    """Turn bare ``http(s)://`` and ``www.`` addresses into links.

    Runs after the inline patterns, on the element tree, so text
    inside existing links, code and preformatted blocks is left alone
    and nothing is ever matched across HTML markup.
    """

    def run(self, root: Element) -> None:
        """Link the bare addresses in every eligible text node.

        Args:
            root: The root element of the parsed document.
        """
        self._walk(root)

    def _walk(self, parent: Element) -> None:
        """Process one element's text, then its children and tails.

        Args:
            parent: The element whose text and children to process.
        """
        if parent.tag in _NO_LINK_TAGS:
            return
        if parent.text and _URL_RE.search(parent.text):
            pieces = _split_urls(parent.text)
            parent.text = pieces[0]
            self._insert(parent, 0, pieces[1:])
        index = 0
        while index < len(parent):
            child = parent[index]
            self._walk(child)
            index += 1
            if child.tail and _URL_RE.search(child.tail):
                pieces = _split_urls(child.tail)
                child.tail = pieces[0]
                index += self._insert(parent, index, pieces[1:])

    @staticmethod
    def _insert(parent: Element, index: int,
                pieces: list[str | Element]) -> int:
        """Insert alternating link elements and tail text.

        Args:
            parent: The element to insert into.
            index: Position of the first new child.
            pieces: ``[link, text, link, text, ...]`` from
                ``_split_urls``, without the leading text.

        Returns:
            The number of elements inserted.
        """
        added = 0
        for i in range(0, len(pieces), 2):
            link = pieces[i]
            link.tail = pieces[i + 1] if i + 1 < len(pieces) else ""
            parent.insert(index + added, link)
            added += 1
        return added


class _FamilySafeExtension(Extension):
    """Escape raw HTML, shift headings and link bare addresses."""

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
        # After "inline" (20), so links and code already exist.
        mdi.treeprocessors.register(_Linkify(mdi), "linkify", 15)


def render_markdown(text: str | None) -> Markup:
    r"""Render Markdown to sanitised HTML safe to put in a page.

    Control characters other than tab, newline and carriage return
    are removed from the input and the output. Python-Markdown uses
    ``\x02`` and ``\x03`` as internal markers, so typed ones could
    otherwise confuse it or end up in the page.

    Args:
        text: The Markdown source, or ``None`` for an empty body.

    Returns:
        The cleaned HTML. Bare URLs are linked, and every link gets
        ``rel="nofollow noopener noreferrer"``.
    """
    html = md.markdown(
        _CONTROL_RE.sub("", text or ""),
        extensions=[
            "sane_lists", "nl2br", "fenced_code", _FamilySafeExtension(),
        ],
        output_format="html",
    )
    html = nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=set(ALLOWED_PROTOCOLS),
        link_rel=LINK_REL,
        strip_comments=True,
    )
    # Python-Markdown can still leave one of its own placeholders
    # (STX, a number, ETX) behind, e.g. in ``<a@b.com\\!>``.
    html = _CONTROL_RE.sub("", _PLACEHOLDER_RE.sub("", html))
    return Markup(html)
