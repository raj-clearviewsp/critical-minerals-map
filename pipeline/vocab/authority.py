"""Read the individually named minerals from the retained official USGS list."""

from html.parser import HTMLParser

from pipeline.vocab.core import VocabularyError, key


class _ListNames(HTMLParser):
    def __init__(self):
        super().__init__()
        self.list_depth = 0
        self.in_anchor = False
        self.value = ""
        self.names = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.list_depth += 1
        if tag == "a" and self.list_depth:
            self.in_anchor = True
            self.value = ""

    def handle_endtag(self, tag):
        if tag == "a" and self.in_anchor:
            self.in_anchor = False
            self.names.append(self.value.strip().rstrip(","))
        if tag == "li":
            self.list_depth = max(0, self.list_depth - 1)

    def handle_data(self, data):
        if self.in_anchor:
            self.value += data


def validate_us_list(html: str, entries: dict) -> list[str]:
    marker = '<div  class="field-text-with-media">'
    if marker not in html:
        raise VocabularyError("USGS list format changed; authority needs review")
    content = html.split(marker, 1)[1].split("</article>", 1)[0]
    parser = _ListNames()
    parser.feed(content)
    observed = {key(name) for name in parser.names}
    defined = {key(e["name"]) for e in entries.values() if e["on_us_list"]}
    if len(observed) != 60 or observed != defined:
        raise VocabularyError("Vocabulary membership does not match all 60 official list names")
    return sorted(parser.names)
