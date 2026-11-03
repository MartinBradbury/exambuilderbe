import re

# Numbered titles ("3.1.10 Foo") sort by their numeric components, then unnumbered titles alphabetically.
NUMERIC_PREFIX = re.compile(r"\d+(?:\.\d+)*")
COMPONENT_WIDTH = 6
MAX_COMPONENTS = 8
SORT_KEY_MAX_LENGTH = 320


def natural_sort_key(title):
    """Deterministic string key giving natural numeric ordering for curriculum titles.

    Numbered: "0" + zero-padded components joined by "." + " " + normalised remainder.
    Unnumbered (or malformed numeric prefix: more than MAX_COMPONENTS components or a
    component longer than COMPONENT_WIDTH digits): "1" + normalised full title.
    A separator of " " sorts before "." so "3.1 Foo" precedes "3.1.1 Bar".
    The longest possible key (8 padded components of 1 digit) is 56 + 1 + 240 characters
    for a 255-character title, below SORT_KEY_MAX_LENGTH.
    """
    text = " ".join(str(title or "").split())
    match = NUMERIC_PREFIX.match(text)
    if match:
        parts = match.group(0).split(".")
        if len(parts) <= MAX_COMPONENTS and all(len(p) <= COMPONENT_WIDTH for p in parts):
            padded = ".".join(str(int(p)).zfill(COMPONENT_WIDTH) for p in parts)
            remainder = text[match.end():].strip().casefold()
            return f"0{padded} {remainder}"
    return f"1{text.casefold()}"
