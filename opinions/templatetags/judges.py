from django import template

register = template.Library()

# Generational suffixes are not the surname ("James B. Morse Jr." -> JM).
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


@register.filter
def initials(full_name):
    """First and last name initials for the no-portrait monogram.

    "Christopher J. O'Neil" -> "CO", "James B. Morse Jr." -> "JM",
    a surname-only byline row ("Becke") -> "B".
    """
    tokens = [t for t in (full_name or "").replace(",", " ").split()
              if t.strip(".").lower() not in _SUFFIXES]
    letters = [t.lstrip("'\"(")[:1] for t in tokens]
    letters = [c for c in letters if c.isalpha()]
    if not letters:
        return ""
    if len(letters) == 1:
        return letters[0].upper()
    return (letters[0] + letters[-1]).upper()
