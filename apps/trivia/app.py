"""Trivia plugin for Split-Flap Display."""

def fetch(settings, format_lines, get_rows, get_cols):
    import urllib.request
    import json
    import html
    import random

    cols = get_cols()
    rows = get_rows()

    fallback_qa = [
        ("What is the largest planet?", "Jupiter"),
        ("How many bones in a human body?", "206"),
        ("What is the speed of light?", "186000 mi/sec"),
        ("What year did WW2 end?", "1945"),
        ("What is the smallest country?", "Vatican City"),
        ("How many strings on a guitar?", "Six"),
        ("What is the hardest mineral?", "Diamond"),
        ("What gas do plants absorb?", "CO2"),
        ("How many legs does a spider have?", "Eight"),
        ("What is the largest ocean?", "Pacific"),
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
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        result = data["results"][0]
        question = html.unescape(result["question"])
        answer = html.unescape(result["correct_answer"])
    except Exception:
        q, a = random.choice(fallback_qa)
        question, answer = q, a

    # No character filtering: the renderer degrades wall-aware at the last moment
    # (accents survive on reels that carry them). Filtering to ASCII here was
    # punching holes in trivia about "Beyoncé" on walls that could have shown her.
    q_lines = split_text(question, cols)
    pages = []
    for i in range(0, len(q_lines), rows):
        chunk = q_lines[i:i + rows]
        pages.append(format_lines(*chunk))

    a_lines = split_text(answer, cols)
    a_lines = ['Answer:'] + a_lines
    for i in range(0, len(a_lines), rows):
        chunk = a_lines[i:i + rows]
        pages.append(format_lines(*chunk))

    return pages or [format_lines('Trivia', 'No data', '')]
