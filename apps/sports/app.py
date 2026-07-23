"""Sports scores via ESPN API — multi-league."""

# =============================================================================
# SHARED — the games: which leagues/teams are followed and every matching game
# from ESPN, one list in one order. Both surfaces render from _gather_games, so
# a wall and a panel always show the same games.
# =============================================================================

LEAGUES = {
    'nfl':    {'path': 'football/nfl',                       'name': 'NFL'},
    'nba':    {'path': 'basketball/nba',                     'name': 'NBA'},
    'mlb':    {'path': 'baseball/mlb',                       'name': 'MLB'},
    'nhl':    {'path': 'hockey/nhl',                         'name': 'NHL'},
    'ncaaf':  {'path': 'football/college-football',          'name': 'NCAAF'},
    'ncaab':  {'path': 'basketball/mens-college-basketball', 'name': 'NCAAB'},
    'mls':    {'path': 'soccer/usa.1',                       'name': 'MLS'},
    'usl':    {'path': 'soccer/usa.usl.1',                   'name': 'USL'},
    'usl1':   {'path': 'soccer/usa.usl.l1',                  'name': 'USL1'},
    'nwsl':   {'path': 'soccer/usa.nwsl',                    'name': 'NWSL'},
    'epl':    {'path': 'soccer/eng.1',                       'name': 'EPL'},
    'laliga': {'path': 'soccer/esp.1',                       'name': 'LaLiga'},
    'ucl':    {'path': 'soccer/uefa.champions',              'name': 'UCL'},
    'uel':    {'path': 'soccer/uefa.europa',                 'name': 'Europa'},
    'ger':    {'path': 'soccer/ger.1',                       'name': 'Bundesliga'},
    'ita':    {'path': 'soccer/ita.1',                       'name': 'Serie A'},
    'fra':    {'path': 'soccer/fra.1',                       'name': 'Ligue 1'},
    'por':    {'path': 'soccer/por.1',                       'name': 'Primeira'},
    'ned':    {'path': 'soccer/ned.1',                       'name': 'Eredivisie'},
    'mex':    {'path': 'soccer/mex.1',                       'name': 'Liga MX'},
    'bra':    {'path': 'soccer/bra.1',                       'name': 'Brasileirao'},
    'efl':    {'path': 'soccer/eng.2',                       'name': 'Championship'},
    'wnba':   {'path': 'basketball/wnba',                    'name': 'WNBA'},
    'ncaaw':  {'path': 'basketball/womens-college-basketball','name': 'NCAAW'},
    'soft':   {'path': 'baseball/college-softball',          'name': 'Softball'},
    'msoc':   {'path': 'soccer/usa.ncaa.m.1',               'name': 'MSOC'},
    'wsoc':   {'path': 'soccer/usa.ncaa.w.1',               'name': 'WSOC'},
    'pga':    {'path': 'golf/pga',                           'name': 'PGA'},
    'ufc':    {'path': 'mma/ufc',                            'name': 'UFC'},
}

def _parse_follows(raw):
    """Parse the ``follows`` chip list into ``{league: set_of_abbrs_or_{'*'}}``.
    Each item is ``"<league>:<ABBR>"`` or ``"<league>:*"``, optionally suffixed
    with ``"|<display label>"`` (ignored here)."""
    by_league = {}
    for item in str(raw or '').split(','):
        item = item.strip()
        if not item:
            continue
        core = item.split('|', 1)[0]            # drop the display label
        league, _, team = core.partition(':')
        league = league.strip().lower()
        team = team.strip().upper()
        if league not in LEAGUES:
            continue
        picks = by_league.setdefault(league, set())
        picks.add('*' if team in ('', '*') else team)
    return by_league


# The active Localizer for the current fetch (set below). Sports has helper functions,
# so a module-level localizer avoids threading it through every signature; the plugin
# runtime serializes fetches per app, so it's set once per render.
_LOC = None


def _t(s):
    return _LOC.t(s, "sports") if _LOC is not None else s


def _gather_games(settings, format_lines, get_cols):
    """Every followed league's matching games, in follow order — the one list both
    surfaces render. Each game carries its flap page AND its raw fields."""
    import requests, logging

    game_filter = settings.get('sports_filter', 'all')
    by_league = _parse_follows(settings.get('follows', ''))

    all_games = []
    for key, picks in by_league.items():
        info = LEAGUES[key]
        show_all = '*' in picks
        team_filter = sorted(t for t in picks if t != '*')
        try:
            games = _fetch_league(key, info, team_filter, show_all, format_lines, get_cols, requests, game_filter)
            all_games.extend(games)
        except Exception as e:
            logging.error(f"ESPN {key} error: {e}")
    return all_games


def _fetch_league(key, info, team_filter, show_all, format_lines, get_cols, requests, game_filter):
    from datetime import datetime, timedelta
    # Expand date range to catch recent finals and upcoming games
    start = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
    end = (datetime.now() + timedelta(days=7)).strftime('%Y%m%d')
    url = f"https://site.api.espn.com/apis/site/v2/sports/{info['path']}/scoreboard?dates={start}-{end}&limit=100"
    data = requests.get(url, timeout=8).json()
    events = data.get('events', [])
    if key == 'pga':
        return [{'page': p, 'score_line': '', 'status': ''} for p in _golf(events, info, format_lines)]
    if key == 'ufc':
        return [{'page': p, 'score_line': '', 'status': ''} for p in _mma(events, info, format_lines)]

    games = _parse_events(events, info, team_filter, show_all, format_lines, get_cols, game_filter)

    # If no games found from scoreboard, check team schedules for most recent game
    if not games and not show_all:
        seen_teams = set()
        for abbr in team_filter:
            if abbr in seen_teams:
                continue
            seen_teams.add(abbr)
            recent = _fetch_last_game(info, abbr, format_lines, get_cols, requests, game_filter)
            if recent:
                games.append(recent)

    return games


def _fetch_last_game(info, team_abbr, format_lines, get_cols, requests, game_filter):
    """Fetch the most recent game for a team via the teams endpoint.
    Always returns the last completed game regardless of filter."""
    import logging
    try:
        teams_url = f"https://site.api.espn.com/apis/site/v2/sports/{info['path']}/teams?limit=500"
        team_id = None
        for page in range(1, 4):
            url = f"{teams_url}&page={page}"
            data = requests.get(url, timeout=8).json()
            batch = data.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
            if not batch:
                break
            for entry in batch:
                t = entry.get('team', entry)
                if t.get('abbreviation', '').upper() == team_abbr:
                    team_id = t.get('id')
                    break
            if team_id:
                break
        if not team_id:
            return None

        sched_url = f"https://site.api.espn.com/apis/site/v2/sports/{info['path']}/teams/{team_id}/schedule"
        sched = requests.get(sched_url, timeout=8).json()
        events = sched.get('events', [])

        for event in reversed(events):
            comps = event.get('competitions', [])
            if not comps:
                continue
            comp = comps[0]
            state = comp.get('status', {}).get('type', {}).get('state', 'pre')
            if state != 'post':
                continue
            competitors = comp.get('competitors', [])
            if len(competitors) < 2:
                continue
            away = home = None
            for c in competitors:
                if c.get('homeAway') == 'home': home = c
                else: away = c
            if not away or not home:
                continue
            aa = away['team'].get('abbreviation', '???').upper()
            ha = home['team'].get('abbreviation', '???').upper()
            as_ = away.get('score', {})
            hs_ = home.get('score', {})
            a_score = as_.get('displayValue', str(as_)) if isinstance(as_, dict) else str(as_)
            h_score = hs_.get('displayValue', str(hs_)) if isinstance(hs_, dict) else str(hs_)
            score_line = f"{aa} {a_score}  {ha} {h_score}"
            page = format_lines(info['name'], score_line, _t("Final"))
            return {'page': page, 'score_line': score_line, 'status': _t("Final"),
                    'state': 'post', 'league': info['name'],
                    'away': (aa, a_score), 'home': (ha, h_score)}
        return None
    except Exception as e:
        logging.error(f"Schedule fetch error for {team_abbr}: {e}")
        return None


def _parse_events(events, info, team_filter, show_all, format_lines, get_cols, game_filter):
    live, upcoming, final = [], [], []
    for event in events:
        comp = event.get('competitions', [{}])[0]
        competitors = comp.get('competitors', [])
        if len(competitors) < 2:
            continue
        away = home = None
        for c in competitors:
            if c.get('homeAway') == 'home': home = c
            else: away = c
        if not away or not home:
            continue
        aa = away['team'].get('abbreviation', '???').upper()
        ha = home['team'].get('abbreviation', '???').upper()
        if not show_all and aa not in team_filter and ha not in team_filter:
            continue
        state = event.get('status', {}).get('type', {}).get('state', 'pre')
        detail = event.get('status', {}).get('type', {}).get('shortDetail', '')

        # Apply game filter
        if game_filter == 'live' and state != 'in':
            continue
        elif game_filter == 'live+upcoming' and state == 'post':
            continue
        elif game_filter == 'live+final' and state == 'pre':
            continue

        if state == 'pre':
            score_line = f"{aa} vs {ha}"
        else:
            score_line = f"{aa} {away.get('score','0')}  {ha} {home.get('score','0')}"
        status = _t("Final") if state == 'post' else detail[:15]
        page = format_lines(info['name'], score_line, status)
        game = {'page': page, 'score_line': score_line, 'status': status,
                'state': state, 'league': info['name'],
                # structured fields for pixel surfaces (the flap page ignores them)
                'away': (aa, str(away.get('score', '') or '')),
                'home': (ha, str(home.get('score', '') or ''))}
        (live if state == 'in' else upcoming if state == 'pre' else final).append(game)
    return live + upcoming + final

def _golf(events, info, format_lines):
    if not events:
        return [format_lines("PGA Tour", _t("No event"), _t("This week"))]
    comps = events[0].get('competitions', [{}])
    if not comps:
        return [format_lines("PGA Tour", "No data", "")]
    competitors = comps[0].get('competitors', [])
    competitors.sort(key=lambda c: int(c.get('order', 999)))
    pages = []
    for i in range(0, min(9, len(competitors)), 3):
        chunk = competitors[i:i+3]
        lines = []
        for c in chunk:
            name = c.get('athlete', {}).get('shortName', '?')
            score = c.get('score', {}).get('displayValue', '') if isinstance(c.get('score'), dict) else str(c.get('score', ''))
            lines.append(f"{c.get('order','?')} {name[:8]} {score}"[:15])
        while len(lines) < 3: lines.append('')
        pages.append(format_lines(*lines))
    return pages or [format_lines("PGA Tour", _t("No leaders"), "")]

def _mma(events, info, format_lines):
    if not events:
        return [format_lines("UFC", _t("No event"), _t("Scheduled"))]
    pages = []
    for comp in events[0].get('competitions', [])[:5]:
        competitors = comp.get('competitors', [])
        if len(competitors) < 2: continue
        n1 = competitors[0].get('athlete', {}).get('shortName', '?')[:7]
        n2 = competitors[1].get('athlete', {}).get('shortName', '?')[:7]
        state = comp.get('status', {}).get('type', {}).get('state', 'pre')
        detail = comp.get('status', {}).get('type', {}).get('shortDetail', '')
        r3 = detail[:15] if detail else (_t("Live") if state == 'in' else _t("Upcoming"))
        pages.append(format_lines("UFC", f"{n1} v {n2}", r3))
    return pages or [format_lines("UFC", _t("No fights"), _t("Scheduled"))]


# =============================================================================
# SPLIT-FLAP — fetch() and its page layouts, unique to the character-grid wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    global _LOC
    _LOC = i18n

    game_filter = settings.get('sports_filter', 'all')
    show_league = settings.get('sports_show_league', 'yes') == 'yes'
    compact = settings.get('sports_compact', 'no') == 'yes'

    by_league = _parse_follows(settings.get('follows', ''))
    if not by_league:
        return [format_lines("Sports", _t("Nothing"), _t("Followed"))]

    all_games = _gather_games(settings, format_lines, get_cols)

    if not all_games:
        filter_labels = {'all': _t('All'), 'live': _t('Live'),
                         'live+upcoming': f"{_t('Live')}/{_t('Upcoming')}",
                         'live+final': f"{_t('Live')}/{_t('Final')}"}
        return [format_lines("Sports", _t("No games"), filter_labels.get(game_filter, _t('Found')))]

    rows = get_rows()
    cols = get_cols()

    # Wide wall, standard layout: a TABLE — one game per row, league / score / status in
    # aligned columns, several games to a page, instead of one game on a 3-line page with
    # the width to spare. (Golf and UFC bring their own multi-line pages, so this only
    # kicks in when every game is a regular team score, and only when the row fits.)
    if not compact and rows >= 2:
        tabular = [g for g in all_games if g.get('score_line')]
        if tabular and len(tabular) == len(all_games):
            lw = max((len(g.get('league', '')) for g in tabular), default=0) if show_league else 0
            sw = max(len(g['score_line']) for g in tabular)
            tw = max(len(g['status']) for g in tabular)
            gap = 2
            row_w = (lw + gap if lw else 0) + sw + gap + tw
            if row_w <= cols:
                gg = ' ' * gap
                lines = []
                for g in tabular:
                    parts = ([g.get('league', '').ljust(lw)] if lw else []) + \
                            [g['score_line'].ljust(sw), g['status'].rjust(tw)]
                    lines.append(gg.join(parts))
                return [format_lines(*lines[i:i + rows]) for i in range(0, len(lines), rows)]

    if rows == 1:
        # Just score line
        return [g['score_line'][:cols].center(cols) for g in all_games]

    if not compact:
        if rows == 2:
            # score + status, no league name
            return [g['score_line'][:cols].center(cols) + g['status'][:cols].center(cols) for g in all_games]
        # 3+ rows: standard layout
        if show_league:
            return [g['page'] for g in all_games]
        else:
            return [g['score_line'][:cols].center(cols) + (' ' * cols) + g['status'][:cols].center(cols) for g in all_games]

    # Compact mode: games_per_page = rows - 1 (leave 1 row for statuses), min 1
    games_per_page = max(1, rows - 1)
    pages = []
    for i in range(0, len(all_games), games_per_page):
        chunk = all_games[i:i+games_per_page]
        score_rows = [g['score_line'][:cols].center(cols) for g in chunk]
        # pad to games_per_page
        while len(score_rows) < games_per_page:
            score_rows.append(' ' * cols)
        if rows > games_per_page:
            # status row
            statuses = [g['status'][:max(1, cols // games_per_page)] for g in chunk]
            status_row = ''.join(s.ljust(cols // games_per_page) for s in statuses)[:cols]
            pages.append(''.join(score_rows) + status_row)
        else:
            pages.append(''.join(score_rows))
    return pages

def trigger(settings, conditions):
    """Fire when a followed team's game matches the configured event condition."""
    import requests
    from datetime import datetime, timedelta

    event_type = conditions.get('event', 'game_start')
    teams_str = conditions.get('teams', '').strip()
    trigger_teams = {t.strip().upper() for t in teams_str.split(',') if t.strip()} if teams_str else None

    followed = _parse_follows(settings.get('follows', ''))

    # Build set of all followed teams if no specific teams configured
    if not trigger_teams:
        trigger_teams = set()
        for picks in followed.values():
            if '*' in picks:
                trigger_teams.add('*')          # follow-all league
            trigger_teams.update(t for t in picks if t != '*')

    if not trigger_teams:
        return False

    state_obj = getattr(trigger, '_state', None)
    if state_obj is None:
        state_obj = {'seen_game_ids': set(), 'last_scores': {}}
        setattr(trigger, '_state', state_obj)

    # Bound the bookkeeping so it can't grow without limit over a long uptime.
    if len(state_obj['seen_game_ids']) > 1000:
        state_obj['seen_game_ids'].clear()
    if len(state_obj['last_scores']) > 1000:
        state_obj['last_scores'].clear()

    try:
        start = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
        end = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
        for key in followed:
            if key in ('pga', 'ufc'):
                continue
            info = LEAGUES[key]
            url = f"https://site.api.espn.com/apis/site/v2/sports/{info['path']}/scoreboard?dates={start}-{end}&limit=50"
            data = requests.get(url, timeout=8).json()
            for event in data.get('events', []):
                comp = event.get('competitions', [{}])[0]
                competitors = comp.get('competitors', [])
                if len(competitors) < 2:
                    continue
                away = home = None
                for c in competitors:
                    if c.get('homeAway') == 'home': home = c
                    else: away = c
                if not away or not home:
                    continue
                aa = away['team'].get('abbreviation', '').upper()
                ha = home['team'].get('abbreviation', '').upper()
                if '*' not in trigger_teams and aa not in trigger_teams and ha not in trigger_teams:
                    continue
                game_id = event.get('id', '')
                state = event.get('status', {}).get('type', {}).get('state', 'pre')
                a_score = int(away.get('score', 0) or 0)
                h_score = int(home.get('score', 0) or 0)
                score_key = f"{game_id}"

                if event_type == 'game_start':
                    if state == 'in' and game_id not in state_obj['seen_game_ids']:
                        state_obj['seen_game_ids'].add(game_id)
                        return True

                elif event_type == 'score_change':
                    if state == 'in':
                        prev = state_obj['last_scores'].get(score_key)
                        curr = (a_score, h_score)
                        state_obj['last_scores'][score_key] = curr
                        if prev and prev != curr:
                            return True

                elif event_type == 'close_game':
                    if state == 'in' and abs(a_score - h_score) <= 5:
                        if score_key not in state_obj['seen_game_ids']:
                            state_obj['seen_game_ids'].add(score_key)
                            return True

                elif event_type == 'final':
                    if state == 'post' and game_id not in state_obj['seen_game_ids']:
                        state_obj['seen_game_ids'].add(game_id)
                        return True

                elif event_type == 'overtime':
                    detail = event.get('status', {}).get('type', {}).get('shortDetail', '').upper()
                    ot_keywords = ('OT', 'OVERTIME', 'EXTRA', 'SHOOTOUT', 'PENALTY')
                    if state == 'in' and any(k in detail for k in ot_keywords):
                        ot_key = f"ot_{game_id}"
                        if ot_key not in state_obj['seen_game_ids']:
                            state_obj['seen_game_ids'].add(ot_key)
                            return True

                elif event_type == 'playoff':
                    notes = comp.get('notes', [])
                    is_playoff = any('playoff' in str(n).lower() or 'postseason' in str(n).lower() for n in notes)
                    if not is_playoff:
                        season_type = event.get('season', {}).get('type', 2)
                        is_playoff = season_type in (3, 4)  # 3=postseason, 4=offseason
                    if is_playoff and state == 'in' and game_id not in state_obj['seen_game_ids']:
                        state_obj['seen_game_ids'].add(game_id)
                        return True

                elif event_type == 'comeback':
                    if state == 'in':
                        margin = int(conditions.get('comeback_margin', 10))
                        prev = state_obj['last_scores'].get(score_key)
                        curr = (a_score, h_score)
                        state_obj['last_scores'][score_key] = curr
                        if prev:
                            prev_diff = prev[0] - prev[1]
                            curr_diff = curr[0] - curr[1]
                            if prev_diff <= -margin and abs(curr_diff) <= 3:
                                comeback_key = f"comeback_a_{game_id}"
                                if comeback_key not in state_obj['seen_game_ids']:
                                    state_obj['seen_game_ids'].add(comeback_key)
                                    return True
                            if prev_diff >= margin and abs(curr_diff) <= 3:
                                comeback_key = f"comeback_h_{game_id}"
                                if comeback_key not in state_obj['seen_game_ids']:
                                    state_obj['seen_game_ids'].add(comeback_key)
                                    return True

                elif event_type == 'rival':
                    rivals_str = conditions.get('rivals', '').strip()
                    rivals = {r.strip().upper() for r in rivals_str.split(',') if r.strip()}
                    if rivals and state == 'in' and game_id not in state_obj['seen_game_ids']:
                        if (aa in trigger_teams and ha in rivals) or (ha in trigger_teams and aa in rivals):
                            state_obj['seen_game_ids'].add(game_id)
                            return True

                elif event_type == 'shutout':
                    if state == 'in':
                        # Fire when one team has 0 and the other has scored
                        if (a_score == 0 and h_score > 0) or (h_score == 0 and a_score > 0):
                            shutout_key = f"shutout_{game_id}"
                            if shutout_key not in state_obj['seen_game_ids']:
                                state_obj['seen_game_ids'].add(shutout_key)
                                return True

    except Exception:
        raise
    return False


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A scoreboard card, one game per hold: the two teams as big rows with their
# scores, the league up top and the status color-coded (live green, upcoming
# amber, final gray; the winner stays bright, the loser dims). Rotates through
# the SAME games the flap pages show, from the same gather. Black background.
# =============================================================================

_MX_WHITE = (240, 240, 244)
_MX_GRAY = (150, 150, 158)
_MX_DIM = (128, 128, 136)                   # the losing side
_MX_LIVE = (90, 220, 120)                   # in progress
_MX_PRE = (255, 180, 60)                    # scheduled
_MX_RULE = (48, 52, 62)                     # thin dividers


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (nothing followed / no games)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_MX_WHITE)
    if line2:
        y += h1 + gap
        draw.text(((W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1]), line2, font=f2, fill=_MX_GRAY)
    return img


def _mx_status_color(state):
    return {'in': _MX_LIVE, 'pre': _MX_PRE}.get(state, _MX_GRAY)


def _mx_team_rows(game):
    """[(abbr, score), (abbr, score)] from the structured fields, or None (golf/UFC)."""
    away, home = game.get('away'), game.get('home')
    if not away or not home:
        return None
    return [away, home]


def _mx_text_card(canvas, draw, lines, top, height):
    """Golf and UFC pages carry their own lines — a typographic stack whose first
    line hugs the region's top and last line sits on its bottom."""
    W = canvas.width
    lines = [ln for ln in lines if str(ln).strip()][:3] or ['']
    lh = height // len(lines)
    for i, ln in enumerate(lines):
        f = _cv_fit(canvas, ln, W - 6, lh - 2)
        b = f.getbbox(ln)
        if i == 0:
            ty = top - b[1]
        elif i == len(lines) - 1:
            ty = top + height - b[3]
        else:
            ty = top + i * lh + (lh - (b[3] - b[1])) / 2.0 - b[1]
        draw.text(((W - f.getlength(ln)) / 2.0, ty), ln, font=f, fill=_MX_WHITE)
    return canvas


def _mx_scoreboard(canvas, draw, game, top, height, rule=True, even=False):
    """The two team rows: abbreviation left, score right (or a VS mark pre-game).
    ``even`` spreads the rows with equal air instead of anchoring the home row to
    the band's floor — used on short panels where a status line follows beneath,
    so away / home / status read as three evenly spaced rows."""
    W = canvas.width
    rows = _mx_team_rows(game)
    state = game.get('state', 'pre')
    row_h = height // 2

    # One size for everything: the widest cell at the row height decides it.
    cells = [c for pair in rows for c in pair if c]
    probe = max(cells, key=len) if cells else '0'
    f = _cv_fit(canvas, probe, int(W * 0.55), row_h - 2)
    b = f.getbbox('AG0')

    # post: the winner stays bright, the loser dims (a tie keeps both lit)
    colors = [_MX_WHITE, _MX_WHITE]
    if state == 'post':
        try:
            a, h = int(rows[0][1]), int(rows[1][1])
            if a != h:
                colors = [_MX_WHITE, _MX_DIM] if a > h else [_MX_DIM, _MX_WHITE]
        except (TypeError, ValueError):
            pass

    for i, ((abbr, score), col) in enumerate(zip(rows, colors)):
        # the away row hugs the region's top, the home row sits on its bottom —
        # the scoreboard spends the whole band it was given (``even``: the home
        # row floats so the gap above it matches the gap to the status below)
        if i == 0:
            ty = top - b[1]
        elif even:
            ink = b[3] - b[1]
            ty = top + ink + max(0, height - 2 * ink + 1) // 2 - b[1]
        else:
            ty = top + height - b[3]
        draw.text((3, ty), abbr, font=f, fill=col)
        if score:
            draw.text((W - 3 - f.getlength(score), ty), score, font=f, fill=col)
    if rule:
        draw.line([(3, top + row_h), (W - 4, top + row_h)], fill=_MX_RULE)
    if state == 'pre':
        vs = 'VS'
        vf = _cv_fit(canvas, vs, int(W * 0.25), max(7, int(row_h * 0.5)))
        vb = vf.getbbox(vs)
        vx = W - 3 - vf.getlength(vs)
        vy = top + (2 * row_h - (vb[3] - vb[1])) / 2.0 - vb[1]
        draw.rectangle([vx - 2, vy + vb[1] - 1, vx + vf.getlength(vs) + 1, vy + vb[3] + 1], fill=(0, 0, 0))
        draw.text((vx, vy), vs, font=vf, fill=_MX_GRAY)


def fetch_matrix(settings, canvas, i18n=None):
    """Draw one followed game per hold as a scoreboard card, advancing each redraw. ESPN is
    polled at most once a minute (a cached gather); each game holds ~8s."""
    global _LOC
    _LOC = i18n
    import time
    from PIL import ImageDraw

    if not _parse_follows(settings.get('follows', '')):
        canvas.frame(_cv_message(canvas, ImageDraw, 'SPORTS', 'NOTHING FOLLOWED'))
        return 120.0

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'sig': None, 'ts': 0.0, 'games': [], 'i': 0}
        setattr(fetch_matrix, '_state', st)
    sig = (str(settings.get('follows', '')), str(settings.get('sports_filter', 'all')))
    now = time.time()
    if sig != st['sig'] or (now - st['ts']) > 60:
        st['games'] = _gather_games(settings, lambda *lines: '\n'.join(str(x) for x in lines),
                                    lambda: 24)
        st['sig'] = sig
        st['ts'] = now

    games = st['games']
    if not games:
        canvas.frame(_cv_message(canvas, ImageDraw, 'SPORTS', 'NO GAMES FOUND'))
        return 120.0

    idx = st['i'] % len(games)
    st['i'] = (st['i'] + 1) % len(games)
    game = games[idx]

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    league = str(game.get('league', '') or '')
    status = str(game.get('status', '') or '').upper()
    state = game.get('state', 'pre')
    scol = _mx_status_color(state)

    top, bottom = 1, H
    even_rows = False
    if H >= 48:
        # Header: league left, status right, a live dot when the game is on. Ink
        # tops are PINNED to y=1 — the top row works and glyph tops never clip.
        head_h = max(12, int(H * 0.22))
        if league:
            lf = _cv_fit(canvas, league.upper(), int(W * 0.4), head_h - 2)
            lb = lf.getbbox(league.upper())
            draw.text((3, 1 - lb[1]), league.upper(), font=lf, fill=_MX_GRAY)
        if status:
            sf = _cv_fit(canvas, status, int(W * 0.5), head_h - 2)
            sb = sf.getbbox(status)
            sx = W - 3 - sf.getlength(status)
            draw.text((sx, 1 - sb[1]), status, font=sf, fill=scol)
            if state == 'in':
                cy = 1 + (sb[3] - sb[1]) // 2
                draw.ellipse([sx - 7, cy - 2, sx - 3, cy + 2], fill=_MX_LIVE)
        draw.line([(2, head_h + 1), (W - 3, head_h + 1)], fill=_MX_RULE)
        top = head_h + 3
    elif status:
        # Short panel: the status is the third row, its ink on H-1 — the two team
        # rows above spread with matching air instead of floor-anchoring onto it.
        foot_h = max(7, int(H * 0.22))
        sf = _cv_fit(canvas, status, W - 6, foot_h)
        sb = sf.getbbox(status)
        if (sb[3] - sb[1]) >= 5:
            draw.text(((W - sf.getlength(status)) / 2.0, H - sb[3]),
                      status, font=sf, fill=scol)
            bottom = H - 1 - (sb[3] - sb[1])   # the band ends where the status ink begins
            even_rows = True

    if _mx_team_rows(game) is not None:
        _mx_scoreboard(canvas, draw, game, top, bottom - top, rule=H >= 48, even=even_rows)
    else:
        _mx_text_card(canvas, draw, str(game.get('page', '')).split('\n'), top, bottom - top)

    canvas.frame(img)
    return 8.0 if len(games) > 1 else 30.0
