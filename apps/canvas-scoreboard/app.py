"""Scoreboard — live scores with real team badges, drawn on the panel with canvas ops.

A canvas app: it pulls structured games from ESPN (home/away, scores, status, and each
team's LOGO), downloads and caches the logos, and blits them from the sprite atlas beside big
scores and a status line — rotating one game at a time. A team whose logo can't be fetched
falls back to a colour badge with its abbreviation.

On-device text draws CP1252 glyphs (``_cp`` keeps what the panel can draw) and only has faces
{8,9,10,13,18,20}; the atlas is a single shared slot, so the two badges are re-uploaded every draw.
"""

_MAGENTA = (255, 0, 255)
_FACES = (8, 9, 10, 13, 18, 20)
_SHADOW = (8, 8, 10)

# league code -> ESPN sport path + short name
_LEAGUES = {
    'nfl': ('football/nfl', 'NFL'), 'nba': ('basketball/nba', 'NBA'),
    'mlb': ('baseball/mlb', 'MLB'), 'nhl': ('hockey/nhl', 'NHL'),
    'wnba': ('basketball/wnba', 'WNBA'), 'mls': ('soccer/usa.1', 'MLS'),
    'epl': ('soccer/eng.1', 'EPL'), 'laliga': ('soccer/esp.1', 'LaLiga'),
    'ucl': ('soccer/uefa.champions', 'UCL'), 'uel': ('soccer/uefa.europa', 'Europa'),
    'ger': ('soccer/ger.1', 'Bundesliga'), 'ita': ('soccer/ita.1', 'Serie A'),
    'fra': ('soccer/fra.1', 'Ligue 1'), 'mex': ('soccer/mex.1', 'Liga MX'),
    'ncaaf': ('football/college-football', 'NCAAF'), 'ncaab': ('basketball/mens-college-basketball', 'NCAAB'),
}
_HTTP = {'User-Agent': 'SplitFlapGatewayCompanion/1.0'}


def _face(sz):
    ok = [s for s in _FACES if s <= sz]
    return max(ok) if ok else 8


def _cp(s):
    """Keep CP1252-representable characters (the on-device font's charset)."""
    return str(s).encode('cp1252', 'ignore').decode('cp1252')


def _txt(canvas, x, y, s, color, size, align='left'):
    s = _cp(s)
    canvas.text(x + 1, y + 1, s, _SHADOW, size=size, align=align)
    canvas.text(x, y, s, color, size=size, align=align)


def _hex(c, dflt=(90, 96, 120)):
    try:
        c = str(c).lstrip('#')
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) if len(c) >= 6 else dflt
    except Exception:
        return dflt


def _parse_follow(follow):
    """"nba, epl:ARS" -> [(path, name, {'ARS'}|None), …] keeping the user's order."""
    out = []
    for part in str(follow or 'nba').split(','):
        part = part.strip()
        if not part:
            continue
        code, _, team = part.partition(':')
        lg = _LEAGUES.get(code.strip().lower())
        if not lg:
            continue
        out.append((lg[0], lg[1], {team.strip().upper()} if team.strip() else None))
    return out


def _games(follow, filt):
    """Fetch and flatten the games to show, live first."""
    import datetime
    import requests
    day = datetime.datetime.utcnow().strftime('%Y%m%d')
    order = {'in': 0, 'pre': 1, 'post': 2}
    games = []
    for path, name, teams in _parse_follow(follow):
        try:
            url = f'https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day}'
            data = requests.get(url, timeout=8, headers=_HTTP).json()
        except Exception:
            continue
        for ev in data.get('events', []):
            comp = (ev.get('competitions') or [{}])[0]
            cs = comp.get('competitors', [])
            if len(cs) < 2:
                continue
            home = next((c for c in cs if c.get('homeAway') == 'home'), None)
            away = next((c for c in cs if c.get('homeAway') == 'away'), None)
            if not home or not away:
                continue
            aa = (away['team'].get('abbreviation') or '???').upper()
            ha = (home['team'].get('abbreviation') or '???').upper()
            if teams and aa not in teams and ha not in teams:
                continue
            state = ev.get('status', {}).get('type', {}).get('state', 'pre')
            if filt == 'live' and state != 'in':
                continue
            if filt == 'live+upcoming' and state == 'post':
                continue
            detail = ev.get('status', {}).get('type', {}).get('shortDetail', '') or ''
            games.append({
                'lg': name, 'state': state,
                'status': 'Final' if state == 'post' else _cp(detail)[:16],
                'aa': aa, 'ha': ha,
                'as': str(away.get('score', '') or ('' if state == 'pre' else '0')),
                'hs': str(home.get('score', '') or ('' if state == 'pre' else '0')),
                'alogo': away['team'].get('logo'), 'hlogo': home['team'].get('logo'),
                'ac': _hex(away['team'].get('color')), 'hc': _hex(home['team'].get('color')),
            })
    games.sort(key=lambda g: order.get(g['state'], 3))
    return games


def _logo_tile(url, size, cache):
    """A magenta-keyed square tile of a team logo, or None. Cached by URL."""
    from PIL import Image
    if url in cache:
        base = cache[url]
    else:
        try:
            import io
            import requests
            raw = requests.get(url, timeout=8, headers=_HTTP).content
            base = Image.open(io.BytesIO(raw)).convert('RGBA')
            base.thumbnail((64, 64))
            cache[url] = base
        except Exception:
            cache[url] = None
            return None
    if base is None:
        return None
    fit = base.copy()
    fit.thumbnail((size, size))
    tile = Image.new('RGB', (size, size), _MAGENTA)
    ox, oy = (size - fit.width) // 2, (size - fit.height) // 2
    tile.paste(fit.convert('RGB'), (ox, oy), fit.split()[-1])       # alpha as the paste mask
    return tile


def _badge(canvas, x, y, tile, abbr, color, idx, has_sprite):
    """Draw a team badge at (x,y): the logo sprite if we have one, else a colour chip."""
    if has_sprite:
        canvas.sprite(idx, x, y)
    else:
        canvas.roundrect(x, y, tile, tile, 3, color, fill=True)
        f = _face(tile // 2)
        _txt(canvas, x + tile // 2, y + (tile - f) // 2, abbr[:3], (255, 255, 255), f, align='center')


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    W, H = canvas.width, canvas.height
    use_sprites = bool(getattr(canvas, 'can_sprite', False))

    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'games': [], 'i': 0, 'at': 0.0, 'logos': {}}
        setattr(fetch, '_state', st)

    try:
        rotate = max(3, min(60, int(float(settings.get('rotate', 8) or 8))))
    except (TypeError, ValueError):
        rotate = 8

    # Refresh the game list on the first draw and roughly every couple of minutes.
    import time
    now = time.monotonic()
    if not st['games'] or now - st['at'] > 120:
        st['games'] = _games(settings.get('follow', 'nba'), str(settings.get('filter', 'all') or 'all'))
        st['at'] = now
        st['i'] = 0

    games = st['games']
    canvas.clear((0, 0, 0))                                   # black — team colours pop on unlit pixels

    if not games:
        _txt(canvas, W // 2, H // 2 - 5, 'No games', (210, 216, 232), _face(min(13, H // 3)), align='center')
        canvas.show()
        return 30.0

    g = games[st['i'] % len(games)]
    st['i'] = (st['i'] + 1) % len(games)

    compact = W < 104 or H < 44
    tile = max(12, min(28, int(H * (0.5 if compact else 0.44)))) & ~1
    if compact:
        tile = min(tile, H - 12) & ~1
    ax, hx = 1, W - 1 - tile

    # atlas: away logo -> tile 0, home logo -> tile 1 (re-uploaded every draw; shared slot)
    a_sp = h_sp = False
    if use_sprites:
        at = _logo_tile(g['alogo'], tile, st['logos']) if g['alogo'] else None
        ht = _logo_tile(g['hlogo'], tile, st['logos']) if g['hlogo'] else None
        if at is not None or ht is not None:
            from PIL import Image
            blank = Image.new('RGB', (tile, tile), _MAGENTA)
            canvas.upload_atlas([at or blank, ht or blank])
            a_sp, h_sp = at is not None, ht is not None

    _badge(canvas, ax, 1 if compact else 11, tile, g['aa'], g['ac'], 0, a_sp)
    _badge(canvas, hx, 1 if compact else 11, tile, g['ha'], g['hc'], 1, h_sp)
    score = f"{g['as']}-{g['hs']}" if g['state'] != 'pre' else 'vs'

    if compact:                                              # badges on top, score below
        _txt(canvas, W // 2, 2 + tile, score, (255, 255, 255),
             _face(min(13, max(8, H - tile - 3))), align='center')
        canvas.show()
        return float(rotate)

    by = 11
    _txt(canvas, ax + tile // 2, by + tile + 1, g['aa'], (200, 208, 226), 8, align='center')
    _txt(canvas, hx + tile // 2, by + tile + 1, g['ha'], (200, 208, 226), 8, align='center')
    _txt(canvas, 2, 1, g['lg'], (150, 160, 190), 8)
    _txt(canvas, W - 2, 1, g['status'], (235, 210, 120) if g['state'] == 'in' else (170, 178, 200), 8, align='right')
    if g['state'] == 'pre':
        _txt(canvas, W // 2, by + (tile - 10) // 2, 'vs', (210, 216, 232), _face(10), align='center')
    else:
        sf = _face(min(20, tile))
        mid = W // 2
        _txt(canvas, mid - 3, by + (tile - sf) // 2, g['as'], (255, 255, 255), sf, align='right')
        _txt(canvas, mid, by + (tile - sf) // 2, '-', (150, 160, 190), sf, align='center')
        _txt(canvas, mid + 3, by + (tile - sf) // 2, g['hs'], (255, 255, 255), sf, align='left')
    canvas.show()
    return float(rotate)
