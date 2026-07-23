"""Ticker — one line scrolling across a Matrix panel, rendered ON-DEVICE.

A canvas app that uses the panel's native scrolling ticker (POST /api/canvas/ticker): the
companion sends the text ONCE and the panel scrolls it smoothly itself, nothing streamed
frame by frame — so it stays butter-smooth where a pushed-frame crawl janked. The source is
a custom message or a live RSS feed (its headlines joined into one crawl). On a wall too old
for the native ticker it falls back to a static centred line.
"""

_COLORS = {
    "white": (240, 240, 240), "amber": (255, 176, 0), "green": (0, 220, 80),
    "cyan": (0, 200, 220), "red": (240, 48, 48), "purple": (180, 90, 255),
}


def _headlines(feed_url):
    import urllib.request
    import xml.etree.ElementTree as ET
    req = urllib.request.Request(feed_url, headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        root = ET.fromstring(resp.read())
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    titles = []
    for it in items[:12]:
        el = it.find("title")
        if el is None:
            el = it.find("{http://www.w3.org/2005/Atom}title")
        if el is not None and el.text:
            titles.append(" ".join(el.text.split()))
    return titles


def _static(canvas, text, color):
    """No native ticker on this wall: draw the text as a single centred line, trimmed to fit."""
    from PIL import ImageDraw
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                                   # crisp 1-bit, like the other canvas apps
    f = canvas.font(max(8, int(canvas.height * 0.5)))
    while text and draw.textlength(text, font=f) > canvas.width:
        text = text[:-1]
    bb = draw.textbbox((0, 0), text or " ", font=f)
    draw.text(((canvas.width - (bb[2] - bb[0])) // 2, (canvas.height - (bb[3] - bb[1])) // 2 - bb[1]),
              text, font=f, fill=color)
    canvas.frame(img)


def fetch_matrix(settings, canvas):
    color = _COLORS.get(str(settings.get("ticker_color", "amber")).lower(), _COLORS["amber"])
    try:
        speed = max(1, min(20, int(settings.get("ticker_speed", 4))))
    except (TypeError, ValueError):
        speed = 4

    if str(settings.get("ticker_source", "message")).lower() == "news":
        feed = str(settings.get("feed_url", "") or "https://feeds.bbci.co.uk/news/rss.xml").strip()
        try:
            titles = _headlines(feed)
        except Exception:
            titles = []
        text = "     •     ".join(titles) if titles else "News unavailable — check the feed URL"
        hold = 300.0                                      # re-fetch headlines every 5 minutes
    else:
        text = str(settings.get("ticker_text", "") or "Hello from SplitFlap").strip()
        hold = 3600.0                                     # a fixed message just keeps scrolling

    if getattr(canvas, "can_ticker", False):
        canvas.ticker(text, color, speed)                 # scrolls on-device; we send it once
    else:
        _static(canvas, text, color)
        hold = min(hold, 15.0)
    return hold
