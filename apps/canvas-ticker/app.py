"""News Ticker — the day's headlines gliding across the Matrix panel.

A canvas app (surface: canvas): instead of spelling text on the flap grid, it
renders the top RSS headlines as one long line of crisp anti-aliased type and
scrolls it horizontally, pixel by pixel, like a broadcast news crawl.

The work is done ONCE per refresh, not per frame. The joined headline string is
drawn a single time into a wide off-screen strip (bright warm white text, a
coloured bullet between stories, over a near-black vertical gradient), and each
frame merely crops the visible W-wide window out of that strip and pushes it to
the panel. When the window runs off the right edge it wraps back onto the strip's
own left edge — the strip ends on a separator's blank space and begins on a
headline, so the loop is seamless. The feed is only re-fetched every ~10 minutes;
between fetches the strip is reused and just the scroll offset advances.

The bundled font and panel size come from the injected ``canvas``. Adapts to
64x32, 128x32 and 128x64: the type is sized to the panel, the strip to the text.
"""

_SEP = "   •   "                 # gap + bullet + gap between headlines
_FG = (255, 244, 220)                 # warm white headline text
_ACCENT = (255, 150, 40)              # amber bullet between stories
_BG_TOP = (12, 14, 20)                # near-black, faint cool cast at the top
_BG_BOTTOM = (2, 2, 4)                # to true black at the bottom
_SPEED = 2.0                          # px advanced per frame
_REFRESH_S = 600.0                    # re-fetch the feed at most this often
_DEFAULT_FEED = "https://feeds.bbci.co.uk/news/rss.xml"


def _load_headlines(feed_url):
    """Top ~10 headline strings from an RSS or Atom feed, or None on any failure
    (so the caller can keep the last good set). Whitespace in a title is collapsed
    to a single line — a stray newline would otherwise break the strip."""
    import urllib.request
    import xml.etree.ElementTree as ET
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = root.findall(".//item")                                   # RSS
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")  # Atom
        titles = []
        for item in items[:10]:
            el = item.find("title")
            if el is None:
                el = item.find("{http://www.w3.org/2005/Atom}title")
            if el is not None and el.text:
                text = " ".join(el.text.split())
                if text:
                    titles.append(text)
        return titles or None
    except Exception:
        return None


def _pick_font(canvas, H):
    """The bundled font sized so capital letters fill ~65% of the panel height,
    while a full accent-to-descender span (É … gpqy) still fits within H — so no
    glyph, accented capitals and international feeds included, is ever clipped."""
    target_cap = max(6, int(round(H * 0.66)))
    size = max(8, int(round(target_cap / 0.62)))          # start generous, shrink to fit
    font = canvas.font(size)
    for _ in range(size):
        if size <= 8:
            break
        cap = font.getbbox("HNEXO")
        full = font.getbbox("HÉlgpqy")
        if (cap[3] - cap[1]) <= target_cap and (full[3] - full[1]) <= (H - 1):
            break
        size -= 1
        font = canvas.font(size)
    return font


def _bg_strip(width, H):
    """A `width` x H image with a subtle vertical near-black gradient — built one
    column then stretched, cheap even for a very wide strip."""
    from PIL import Image
    col = Image.new("RGB", (1, H))
    px = col.load()
    span = max(1, H - 1)
    for y in range(H):
        r = y / span
        px[0, y] = (int(_BG_TOP[0] + (_BG_BOTTOM[0] - _BG_TOP[0]) * r),
                    int(_BG_TOP[1] + (_BG_BOTTOM[1] - _BG_TOP[1]) * r),
                    int(_BG_TOP[2] + (_BG_BOTTOM[2] - _BG_TOP[2]) * r))
    return col.resize((max(1, int(width)), H))


def _build_strip(canvas, headlines, font):
    """Render the joined headlines ONCE into a wide (strip_w x H) image and return
    (image, strip_w). The text is tiled enough times to be at least a panel wide so
    the per-frame wrap only ever needs two crops. Each story is drawn in warm white
    and each separator's bullet in amber, all on one baseline."""
    from PIL import Image, ImageDraw
    import math

    W, H = canvas.width, canvas.height
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    unit_w = probe.textlength(_SEP.join(headlines) + _SEP, font=font)
    if unit_w < 1:
        unit_w = float(W)

    reps = 1
    while unit_w * reps < W + 8:            # guarantee strip_w > W for the 2-crop wrap
        reps += 1
    strip_w = int(math.ceil(unit_w * reps)) + 2

    strip = _bg_strip(strip_w, H)
    draw = ImageDraw.Draw(strip)

    # Vertical centre: place the ink box of a tall+low sample in the middle of H,
    # then draw every segment on that same baseline (default 'la' anchor).
    box = font.getbbox("HÉlgpqy")
    y = round((H - (box[3] - box[1])) / 2.0 - box[1])
    sep_w = probe.textlength(_SEP, font=font)

    x = 0.0
    for _ in range(reps):
        for head in headlines:
            draw.text((round(x), y), head, font=font, fill=_FG)
            x += probe.textlength(head, font=font)
            draw.text((round(x), y), _SEP, font=font, fill=_ACCENT)
            x += sep_w
    return strip, strip_w


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    # No canvas (a physical split-flap wall) → this app has nothing to draw on.
    if canvas is None:
        return None

    from datetime import datetime
    from PIL import Image

    W, H = canvas.width, canvas.height
    feed_url = str((settings or {}).get("feed_url") or _DEFAULT_FEED).strip() or _DEFAULT_FEED

    st = getattr(fetch, "_state", None)
    if st is None:
        st = {"offset": 0.0, "headlines": None, "feed_url": None,
              "last_fetch": None, "strip": None, "strip_w": 0, "strip_key": None}
        setattr(fetch, "_state", st)

    # --- refresh the feed at most every ~10 min (or when the URL changes) -------
    now = datetime.now()
    stale = st["last_fetch"] is None or (now - st["last_fetch"]).total_seconds() >= _REFRESH_S
    if st["headlines"] is None or st["feed_url"] != feed_url or stale:
        heads = _load_headlines(feed_url)
        if heads:
            st["headlines"] = heads
        elif st["headlines"] is None:               # never had a good set → placeholder
            st["headlines"] = ["News unavailable"]
        st["feed_url"] = feed_url
        st["last_fetch"] = now
        st["strip"] = None                          # force a rebuild below

    # --- (re)build the wide strip only when headlines or panel size change -------
    headlines = st["headlines"]
    key = (tuple(headlines), W, H)
    if st["strip"] is None or st["strip_key"] != key:
        font = _pick_font(canvas, H)
        st["strip"], st["strip_w"] = _build_strip(canvas, headlines, font)
        st["strip_key"] = key
        if st["strip_w"]:
            st["offset"] %= st["strip_w"]

    strip, strip_w = st["strip"], st["strip_w"]

    # --- crop this frame's visible window, wrapping around the strip's end -------
    off = int(st["offset"]) % strip_w
    if off + W <= strip_w:
        window = strip.crop((off, 0, off + W, H))
    else:
        window = Image.new("RGB", (W, H), (0, 0, 0))
        first = strip_w - off
        window.paste(strip.crop((off, 0, strip_w, H)), (0, 0))
        window.paste(strip.crop((0, 0, W - first, H)), (first, 0))

    canvas.frame(window)
    st["offset"] = (st["offset"] + _SPEED) % strip_w
    return 0.06                                     # smooth; the frame path self-limits (~8 fps)
