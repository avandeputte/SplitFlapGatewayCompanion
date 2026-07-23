"""Scoreboard — live scores with real team badges, drawn on the panel with canvas ops.

A canvas app: it pulls structured games from ESPN (home/away, scores, status, and each
team's LOGO), downloads and caches the logos, and blits them from the sprite atlas beside big
scores and a status line — rotating one game at a time. A team whose logo can't be fetched
falls back to a colour badge with its abbreviation.

Text goes through the injected ``canvas`` (``canvas.shadow_text`` keeps only CP1252 glyphs and
snaps to the faces {8,9,10,13,18,20}); the atlas is a single shared slot, so the two badges are
re-uploaded every draw.
"""

_MAGENTA = (255, 0, 255)

# league code -> ESPN sport path + short name. The keys match the /sports_search picker
# (backend SPORTS_LEAGUES), so a chip picked there routes here; golf/mma aren't two-team
# scoreboards and are intentionally absent (such picks are ignored).
_LEAGUES = {
    'nfl': ('football/nfl', 'NFL'), 'nba': ('basketball/nba', 'NBA'),
    'mlb': ('baseball/mlb', 'MLB'), 'nhl': ('hockey/nhl', 'NHL'),
    'ncaaf': ('football/college-football', 'NCAAF'), 'ncaab': ('basketball/mens-college-basketball', 'NCAAB'),
    'mls': ('soccer/usa.1', 'MLS'), 'usl': ('soccer/usa.usl.1', 'USL'),
    'usl1': ('soccer/usa.usl.l1', 'USL1'), 'nwsl': ('soccer/usa.nwsl', 'NWSL'),
    'epl': ('soccer/eng.1', 'EPL'), 'laliga': ('soccer/esp.1', 'LaLiga'),
    'ucl': ('soccer/uefa.champions', 'UCL'), 'uel': ('soccer/uefa.europa', 'Europa'),
    'ger': ('soccer/ger.1', 'Bundesliga'), 'ita': ('soccer/ita.1', 'Serie A'),
    'fra': ('soccer/fra.1', 'Ligue 1'), 'por': ('soccer/por.1', 'Primeira'),
    'ned': ('soccer/ned.1', 'Eredivisie'), 'mex': ('soccer/mex.1', 'Liga MX'),
    'bra': ('soccer/bra.1', 'Brasileirao'), 'efl': ('soccer/eng.2', 'Championship'),
    'msoc': ('soccer/usa.ncaa.m.1', 'MSOC'), 'wsoc': ('soccer/usa.ncaa.w.1', 'WSOC'),
    'wnba': ('basketball/wnba', 'WNBA'), 'ncaaw': ('basketball/womens-college-basketball', 'NCAAW'),
    'soft': ('baseball/college-softball', 'Softball'),
}
_HTTP = {'User-Agent': 'SplitFlapGatewayCompanion/1.0'}


def _pick_name(canvas, cands, maxw, faces=(10, 9, 8)):
    """(text, face): the fullest candidate that fits `maxw`, at the biggest face it fits — a full
    name at a smaller size beats an abbreviation, so a wide wall shows "Giants", not "SF"."""
    for c in cands:
        c = canvas.cp(c)
        if not c:
            continue
        for f in faces:
            if len(c) * canvas.face_width(f) <= maxw:
                return c, f
    f = faces[-1]
    return (canvas.cp(cands[-1]) if cands else '')[:max(1, maxw // canvas.face_width(f))], f


def _hex(c, dflt=(90, 96, 120)):
    try:
        c = str(c).lstrip('#')
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)) if len(c) >= 6 else dflt
    except Exception:
        return dflt


def _parse_follow(follow):
    """The picker's chip list -> [(path, name, {'ARS'}|None), …], keeping the user's order.

    Each chip is ``league:ABBR|Label`` or ``league:*|Label`` (whole league); the plain
    ``nba`` / ``epl:ARS`` strings the old text field used still parse."""
    out = []
    for part in str(follow or 'nba:*').split(','):
        part = part.split('|', 1)[0].strip()                # drop the picker's display label
        if not part:
            continue
        code, _, team = part.partition(':')
        lg = _LEAGUES.get(code.strip().lower())
        if not lg:
            continue
        team = team.strip().upper()
        out.append((lg[0], lg[1], {team} if team and team != '*' else None))
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
            # Name candidates, fullest first — the layout shows the fullest that fits the wall.
            anm = [n for n in (away['team'].get('displayName'), away['team'].get('shortDisplayName'), aa) if n]
            hnm = [n for n in (home['team'].get('displayName'), home['team'].get('shortDisplayName'), ha) if n]
            state = ev.get('status', {}).get('type', {}).get('state', 'pre')
            if filt == 'live' and state != 'in':
                continue
            if filt == 'live+upcoming' and state == 'post':
                continue
            detail = ev.get('status', {}).get('type', {}).get('shortDetail', '') or ''
            games.append({
                'lg': name, 'state': state,
                'status': 'Final' if state == 'post' else str(detail)[:16],
                'aa': aa, 'ha': ha, 'anm': anm, 'hnm': hnm,
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


def _badge(canvas, x, y, tile, abbr, color, sprite_idx):
    """Draw a team badge at (x,y): the logo sprite (its index in the shared sheet) if we have one,
    else a colour chip with the abbreviation. ``sprite_idx < 0`` means no logo."""
    if sprite_idx >= 0:
        canvas.sprite(sprite_idx, x, y)
    else:
        canvas.roundrect(x, y, tile, tile, 3, color, fill=True)
        f = canvas.face(tile // 2)
        canvas.shadow_text(x + tile // 2, y + (tile - f) // 2, abbr[:3], (255, 255, 255), f, align='center')


def fetch_matrix(settings, canvas):
    W, H = canvas.width, canvas.height
    use_sprites = bool(getattr(canvas, 'can_sprite', False))

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        # `logos`: url -> a magenta-keyed tile (fetched once). `sheet`/`sheet_idx`: ONE shared
        # atlas of every logo across the slate, blitted by index — so the whole app uses a single
        # atlas slot, not one per game. `sheet_for` guards a tile-size change.
        st = {'games': [], 'i': 0, 'at': 0.0, 'logos': {}, 'sheet': [], 'sheet_idx': {},
              'sheet_for': 0, 'key': None}
        setattr(fetch_matrix, '_state', st)

    try:
        rotate = max(3, min(60, int(float(settings.get('rotate', 8) or 8))))
    except (TypeError, ValueError):
        rotate = 8

    # Refresh the game list when the selection CHANGES (so a per-playlist team override takes
    # effect at once, not after a 120 s cache), and otherwise every couple of minutes.
    import time
    now = time.monotonic()
    follow = settings.get('follow', 'nba')
    filt = str(settings.get('filter', 'all') or 'all')
    key = (str(follow), filt)
    if key != st['key'] or now - st['at'] > 120:
        st['games'] = _games(follow, filt)
        st['at'] = now
        st['i'] = 0
        st['key'] = key
        # The shared sheet is NOT reset here: teams recur across refreshes (and across follows),
        # so keeping it lets a returning team re-bind by index instead of re-uploading. It grows
        # only for a genuinely new logo and is bounded by the teams ever shown.

    games = st['games']
    canvas.clear((0, 0, 0))                                   # black — team colours pop on unlit pixels

    if not games:
        canvas.shadow_text(W // 2, H // 2 - 5, 'No games', (210, 216, 232), canvas.face(min(13, H // 3)), align='center')
        canvas.show()
        return 30.0

    g = games[st['i'] % len(games)]
    st['i'] = (st['i'] + 1) % len(games)

    compact = W < 104 or H < 44
    tile = max(12, min(28 if compact else 26, int(H * (0.5 if compact else 0.42)))) & ~1
    if compact:
        tile = min(tile, H - 12) & ~1

    # Compact keeps the badges at the edges (little room to do otherwise); the wide layout brings
    # the two logos together at the middle, team names fanning outward, scores below.
    half = W // 2
    ax, hx = (1, W - 1 - tile) if compact else (half - 2 - tile, half + 2)
    by = 1 if compact else 11

    # ONE shared sheet holds every team's logo, blitted by index — so the whole app occupies a
    # single atlas slot instead of one per game, and never stores a logo twice. The sheet grows
    # lazily as games come round (no fetch burst), and the upload is deduped by the library, so a
    # stable slate settles to one upload. A logo that can't be fetched -> index -1 -> colour chip.
    ai = hi = -1
    if use_sprites:
        if st['sheet_for'] != tile:                     # a size change invalidates the built tiles
            st['sheet'], st['sheet_idx'], st['sheet_for'] = [], {}, tile
        for url in (g['alogo'], g['hlogo']):
            if url and url not in st['sheet_idx']:
                t = _logo_tile(url, tile, st['logos'])
                if t is not None:
                    st['sheet_idx'][url] = len(st['sheet'])
                    st['sheet'].append(t)
        if st['sheet']:
            canvas.upload_atlas(st['sheet'])
        ai = st['sheet_idx'].get(g['alogo'], -1)
        hi = st['sheet_idx'].get(g['hlogo'], -1)

    _badge(canvas, ax, by, tile, g['aa'], g['ac'], ai)
    _badge(canvas, hx, by, tile, g['ha'], g['hc'], hi)

    if compact:                                              # badges on top, score below
        score = f"{g['as']}-{g['hs']}" if g['state'] != 'pre' else 'vs'
        canvas.shadow_text(W // 2, 2 + tile, score, (255, 255, 255),
             canvas.face(min(13, max(8, H - tile - 3))), align='center')
        canvas.show()
        return float(rotate)

    canvas.shadow_text(2, 1, g['lg'], (150, 160, 190), 8)
    canvas.shadow_text(W - 2, 1, g['status'], (235, 210, 120) if g['state'] == 'in' else (170, 178, 200), 8, align='right')

    # team names, fanning outward from the centre logos — the fullest that fits each side.
    an, af = _pick_name(canvas, g['anm'], ax - 5)
    hn, hf = _pick_name(canvas, g['hnm'], W - 2 - (hx + tile + 3))
    canvas.shadow_text(ax - 3, by + (tile - af) // 2, an, (214, 222, 240), af, align='right')
    canvas.shadow_text(hx + tile + 3, by + (tile - hf) // 2, hn, (214, 222, 240), hf, align='left')

    sy = by + tile + 1
    if g['state'] != 'pre':                                  # scores under their logos
        sf = canvas.face(min(18, max(10, H - sy - 1)))
        canvas.shadow_text(ax + tile // 2, sy, g['as'], (255, 255, 255), sf, align='center')
        canvas.shadow_text(hx + tile // 2, sy, g['hs'], (255, 255, 255), sf, align='center')
    canvas.show()
    return float(rotate)
