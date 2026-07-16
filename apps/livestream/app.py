"""Livestream mode — rotates subs, viewers, and comment slides."""

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime
    import pytz, requests, logging

    def t(s):
        return i18n.t(s, "media") if i18n is not None else s

    pages = []
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    # 12h/24h follows the language, not a hardcoded strftime("%I:%M %p").
    time_str = i18n.time(now) if i18n is not None else now.strftime("%I:%M %p").lstrip("0")
    cols = get_cols()

    # YouTube subs
    cid = settings.get('yt_channel_id', '').strip()
    if cid:
        try:
            import urllib.request, json
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode()
            import re
            name = re.search(r'<name>(.+?)</name>', body)
            name = name.group(1) if name else cid[:cols]
            pages.append({'text': format_lines(time_str, name[:cols], "YouTube"), 'style': 'ltr'})
        except Exception:
            pass

    # Concurrent viewers
    api_key = settings.get('yt_api_key', '').strip()
    video_id = settings.get('yt_video_id', '').strip()
    if api_key and video_id:
        try:
            url = f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={api_key}"
            data = requests.get(url, timeout=5).json()
            items = data.get('items', [])
            if items:
                v = items[0].get('liveStreamingDetails', {}).get('concurrentViewers')
                if v is not None:
                    # Grouping follows the language: 1,234 / 1.234 / 1 234.
                    count = i18n.number(int(v), 0) if i18n is not None else f"{int(v):,}"
                    pages.append({'text': format_lines(t("Watching now"), count, t("Live viewers")), 'style': 'diagonal'})
        except Exception:
            pass

    # Comment slides
    raw = settings.get('livestream_comments', '').strip()
    if raw:
        raw = raw.replace('\r\n', '\n').replace('\r', '\n')
        styles = ['outside_in', 'spiral', 'anti_diagonal', 'rtl', 'rain', 'center_out']
        for i, block in enumerate(b for b in raw.split('\n\n') if b.strip()):
            lines = [l.strip() for l in block.split('\n') if l.strip()][:3]
            while len(lines) < 3: lines.append('')
            page = ''.join(l[:cols].center(cols) for l in lines)
            pages.append({'text': page, 'style': styles[i % len(styles)]})

    return pages or [format_lines("Livestream", time_str, t("No data"))]
