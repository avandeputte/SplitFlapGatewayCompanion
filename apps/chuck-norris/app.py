"""Chuck Norris facts plugin for Split-Flap Display."""

def fetch(settings, format_lines, get_rows, get_cols):
    import urllib.request
    import json
    import random

    cols = get_cols()

    fallback = [
        "Chuck Norris counted to infinity twice",
        "Chuck Norris can slam a revolving door",
        "Chuck Norris makes onions cry",
        "When Chuck Norris does pushups he pushes the Earth down",
        "Chuck Norris can hear sign language",
        "Chuck Norris won a staring contest with the sun",
        "Time waits for no one except Chuck Norris",
        "Chuck Norris can cut a knife with butter",
        "Chuck Norris can speak Braille",
        "Chuck Norris beat the sun in a staring contest",
    ]

    def split_text(text, width):
        words = text.split()
        lines = []
        current = ''
        for word in words:
            if current and len(current) + 1 + len(word) > width:
                lines.append(current)
                current = word
            elif not current:
                current = word[:width]
            else:
                current += ' ' + word
        if current:
            lines.append(current)
        return lines

    try:
        url = "https://api.chucknorris.io/jokes/random"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        text = data["value"]
    except Exception:
        text = random.choice(fallback)

    # Remove characters not in the flap set. Case-insensitively: a flap set lists the
    # glyphs a module CARRIES, not the case the wall shows — the companion folds case at
    # the last moment (renderer.fold), and only for a wall with no lowercase flaps. Testing
    # membership case-sensitively would blank every lowercase letter here, long before the
    # display got its say.
    allowed = set(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;:%'.,/?*")
    text = ''.join(c if c.upper() in allowed else ' ' for c in text)

    lines = split_text(text, cols)
    rows = get_rows()
    pages = []
    for i in range(0, len(lines), rows):
        chunk = lines[i:i + rows]
        pages.append(format_lines(*chunk))
    return pages or [format_lines('Chuck Norris', 'Facts', '')]
