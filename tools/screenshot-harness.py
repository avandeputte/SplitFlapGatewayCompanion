"""The screenshot harness — regenerates the whole screenshots/ gallery from app code.

Renders every dual-surface app, channel, and Chomper at the four Matrix panel
resolutions, plus the split-flap wall mockups at three heights, then rebuilds every
contact sheet. All network fetchers are stubbed with sample data; the socket layer is
hard-blocked so nothing can leak out. Run from anywhere:

    python3 tools/screenshot-harness.py               # everything
    python3 tools/screenshot-harness.py weather date  # just these apps (sheets keep the rest)

The committed screenshots/ folder must always mirror the shipped app code — regenerate
after any visual change and commit the changed PNGs.
"""
import importlib.util
import inspect
import json
import os
import socket
import sys
import traceback
from datetime import datetime, timedelta, timezone

# ---- absolutely no network ------------------------------------------------
def _blocked(*a, **k):
    raise RuntimeError('NETWORK BLOCKED by render harness')
socket.getaddrinfo = _blocked
socket.socket.connect = _blocked

from PIL import Image, ImageFont, ImageDraw  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
APPS = os.path.join(ROOT, 'apps')
REVIEW = os.path.join(ROOT, 'screenshots')
FONT_DIR = os.path.join(ROOT, 'backend', 'app', 'fonts')  # == app.canvas._FONT_DIR
_FONT_CACHE = {}

RESOLUTIONS = [(256, 64), (128, 64), (128, 32), (64, 32)]

APP_IDS = [
    'advice', 'aurora', 'binary-clock', 'birdnet', 'bitcoin-fear-greed', 'calendar',
    'cat-facts', 'chuck-norris', 'countdown', 'crypto', 'dashboard', 'date',
    'dog-facts', 'earthquakes', 'entity-board', 'exchange-rates', 'formula1',
    'holidays', 'iss', 'livestream', 'metals', 'metro', 'moon-phase',
    'news-headlines', 'on-this-day', 'planes_overhead', 'quote', 'rocket-launch',
    'sports', 'stocks', 'sun-times', 'tides', 'time-since', 'time', 'trivia',
    'useless-fact', 'weather', 'wiki-today', 'word-clock', 'word-of-the-day',
    'world_clock', 'youtube', 'yt_comments',
    'canvas-chomper',                    # matrix-only: the ops-surface arcade
]

# Channels/quizzes render generically on the panel (big text + themed art) via
# channel_art.render — one representative screen each, motif from the manifest.
CHANNELS = {
    'dad-jokes': ('grin', "What do you call a fish with no eyes?"),
    'fortune-cookie': ('cookie', "A pleasant surprise is waiting for you."),
    'funny-one-liners': ('grin', "I used to think I was indecisive. Now I'm not so sure."),
    'good-morning': ('sun', "Rise and shine — make today count."),
    'good-night': ('moon', "Sleep tight — see you tomorrow."),
    'harry-potter-quotes': ('bolt', "It does not do to dwell on dreams and forget to live.\n— Dumbledore"),
    'magic-8-ball': ('eightball', "Without a doubt."),
    'motivational-quotes': ('quote', "The best way out is always through.\n— Robert Frost"),
    'movie-quotes': ('clapperboard', "Here's looking at you, kid.\n— Casablanca"),
    'office-quotes': ('mug', "Bears. Beets. Battlestar Galactica.\n— Jim Halpert"),
    'sarcastic-fortune-cookies': ('cookie', "Help! I'm trapped in a fortune cookie factory."),
    'shower-thoughts': ('shower', "Your age is just how many laps you've done around the sun."),
    'star-wars-quotes': ('saber', "Do. Or do not. There is no try.\n— Yoda"),
    'stoic-quotes': ('column', "We suffer more often in imagination than in reality.\n— Seneca"),
}
APP_IDS = APP_IDS + sorted(CHANNELS)


# ---- the capture surface ---------------------------------------------------
def _rgb(c):
    if isinstance(c, (tuple, list)):
        return tuple(int(x) for x in c[:3])
    s = str(c).strip()
    if s.startswith('#'):
        s = s[1:]
        if len(s) == 3:
            s = ''.join(ch * 2 for ch in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    from PIL import ImageColor
    return ImageColor.getrgb(s)


_FACES = (8, 9, 10, 13, 18, 20)          # == app.canvas._FACES (bundled on-device faces)
_FACE_W = {8: 5, 9: 6, 10: 6, 13: 8, 18: 9, 20: 10}


class Cap:
    """Stand-in for CanvasSurface: width/height + the Pillow helpers + a frame() that
    captures, plus an ops->PIL shim (clear/roundrect/sprite/shadow_text/...) that
    approximately rasterizes what an ops app renders on-device — black bg, 1-bit
    DejaVu text at the face size, magenta-transparent sprite atlas."""

    def __init__(self, w, h):
        self.width, self.height = int(w), int(h)
        self.frames = []
        # ops-app surface state
        self.can_sprite = True
        self._img = None
        self._atlas = []

    def font(self, size, name='DejaVuSans-Bold.ttf'):
        key = (name, max(5, int(size)))
        f = _FONT_CACHE.get(key)
        if f is None:
            f = ImageFont.truetype(os.path.join(FONT_DIR, name), key[1])
            _FONT_CACHE[key] = f
        return f

    def blank(self, color=(0, 0, 0)):
        return Image.new('RGB', (self.width, self.height), _rgb(color))

    def vgrad(self, top, bottom):
        t, b = _rgb(top), _rgb(bottom)
        col = Image.new('RGB', (1, self.height))
        px = col.load()
        h = max(1, self.height - 1)
        for y in range(self.height):
            r = y / h
            px[0, y] = (int(t[0] + (b[0] - t[0]) * r),
                        int(t[1] + (b[1] - t[1]) * r),
                        int(t[2] + (b[2] - t[2]) * r))
        return col.resize((self.width, self.height))

    def frame(self, image):
        if isinstance(image, (bytes, bytearray)):
            self.frames.append(Image.frombytes('RGB', (self.width, self.height), bytes(image)))
            return True
        self.frames.append(image.convert('RGB').resize((self.width, self.height)))
        return True

    _OPS35 = ('clear', 'pixel', 'hline', 'vline', 'line', 'rect', 'circle', 'ellipse',
              'triangle', 'roundrect', 'gradient', 'polyline', 'poly', 'arc', 'clip',
              'origin', 'text', 'textbox', 'image', 'sprite', 'scroll', 'show')

    def has_op(self, name):
        return name in self._OPS35

    @staticmethod
    def num(settings, key, default, lo=None, hi=None):
        # Mirrors CanvasSurface.num — the raw-string-tolerant clamped settings read.
        try:
            v = float(settings.get(key, default) or default)
        except (TypeError, ValueError, AttributeError):
            v = float(default)
        if lo is not None:
            v = max(float(lo), v)
        if hi is not None:
            v = min(float(hi), v)
        return int(v) if isinstance(default, int) else v

    def pixel(self, x, y, color=(255, 255, 255)):
        self._draw().point((int(x), int(y)), fill=_rgb(color))
        return self

    def rect(self, x, y, w, h, color=(255, 255, 255), fill=False, t=1):
        d = self._draw()
        box = [int(x), int(y), int(x + w - 1), int(y + h - 1)]
        if fill:
            d.rectangle(box, fill=_rgb(color))
        else:
            d.rectangle(box, outline=_rgb(color), width=max(1, int(t)))
        return self

    def circle(self, x, y, r, color=(255, 255, 255), fill=False, t=1):
        d = self._draw()
        box = [int(x - r), int(y - r), int(x + r), int(y + r)]
        if fill:
            d.ellipse(box, fill=_rgb(color))
        else:
            d.ellipse(box, outline=_rgb(color), width=max(1, int(t)))
        return self

    def triangle(self, x, y, x1, y1, x2, y2, color=(255, 255, 255), fill=False):
        d = self._draw()
        pts = [(int(x), int(y)), (int(x1), int(y1)), (int(x2), int(y2))]
        if fill:
            d.polygon(pts, fill=_rgb(color))
        else:
            d.polygon(pts, outline=_rgb(color))
        return self

    def line(self, x, y, x1, y1, color=(255, 255, 255), t=1):
        self._draw().line([(int(x), int(y)), (int(x1), int(y1))],
                          fill=_rgb(color), width=max(1, int(t)))
        return self

    def arc(self, x, y, r, start, end, color=(255, 255, 255), t=2, fill=False):
        d = self._draw()
        box = [int(x - r), int(y - r), int(x + r), int(y + r)]
        # firmware: 0 deg at 12 o'clock, clockwise; PIL: 0 deg at 3 o'clock, clockwise
        a0, a1 = start - 90, end - 90
        if fill:
            d.pieslice(box, a0, a1, fill=_rgb(color))
        else:
            d.arc(box, a0, a1, fill=_rgb(color), width=max(1, int(t)))
        return self

    def poly(self, points, color=(255, 255, 255), fill=True, t=1):
        d = self._draw()
        pts = [(int(px), int(py)) for px, py in points]
        if fill:
            d.polygon(pts, fill=_rgb(color))
        else:
            d.polygon(pts, outline=_rgb(color), width=max(1, int(t)))
        return self

    # ---- ops -> PIL shim (approximates the on-device renderer) --------------
    def _draw(self):
        if self._img is None:
            self._img = Image.new('RGB', (self.width, self.height), (0, 0, 0))
        d = ImageDraw.Draw(self._img)
        d.fontmode = '1'                     # crisp 1-bit text, like the panel
        return d

    def clear(self, color=(0, 0, 0)):
        self._img = Image.new('RGB', (self.width, self.height), _rgb(color))
        return self

    def roundrect(self, x, y, w, h, r, color=(255, 255, 255), fill=False):
        d = self._draw()
        box = [int(x), int(y), int(x) + int(w) - 1, int(y) + int(h) - 1]
        if fill:
            d.rounded_rectangle(box, radius=int(r), fill=_rgb(color))
        else:
            d.rounded_rectangle(box, radius=int(r), outline=_rgb(color), width=1)
        return self

    def upload_atlas(self, images, fmt='rgb888', persist=False):
        self._atlas = [im.convert('RGB') for im in images]
        return True

    def sprite(self, i, x, y):
        # Fidelity note: this gallery shim ignores the real op's flip/rot/scale transforms
        # (and has no compositing/alpha), so sprite-transform and glow visuals render plain
        # here. It never touches encode_ops_bin or the transport, so it can't surface
        # encoding/routing bugs — it's a parallel render path for the static gallery only.
        if not (0 <= int(i) < len(self._atlas)):
            return self
        tile = self._atlas[int(i)]
        mask = Image.new('L', tile.size, 0)
        tp, mp = tile.load(), mask.load()
        for yy in range(tile.size[1]):       # magenta is transparent
            for xx in range(tile.size[0]):
                if tp[xx, yy] != (255, 0, 255):
                    mp[xx, yy] = 255
        self._draw()                         # ensure the panel image exists
        self._img.paste(tile, (int(x), int(y)), mask)
        return self

    @property
    def faces(self):
        return _FACES

    def face(self, size):
        ok = [s for s in _FACES if s <= size]
        return max(ok) if ok else _FACES[0]

    def face_width(self, face):
        return _FACE_W.get(int(face), _FACE_W[_FACES[0]])

    def fit(self, text, maxw, maxh):
        best = _FACES[0]
        for f in _FACES:
            if f <= maxh and len(text) * _FACE_W[f] <= maxw:
                best = f
        return best

    def cp(self, s):
        return str(s).encode('cp1252', 'ignore').decode('cp1252')

    def text(self, x, y, s, color=(255, 255, 255), size=10, align='left', font=None):
        d = self._draw()
        anchor = {'left': 'la', 'center': 'ma', 'right': 'ra'}.get(align, 'la')
        d.text((int(x), int(y)), str(s), font=self.font(int(size)),
               fill=_rgb(color), anchor=anchor)
        return self

    def shadow_text(self, x, y, s, color, size, align='left', shadow=(0, 0, 0)):
        s = self.cp(s)
        if not s:
            return self
        self.text(x + 1, y + 1, s, shadow, size=size, align=align)
        self.text(x, y, s, color, size=size, align=align)
        return self

    def show(self):
        self._draw()
        self.frames.append(self._img.copy())
        return True


# ---- shared helper stubs ---------------------------------------------------
def stub_get_location():
    return {'lat': 42.36, 'lon': -71.05, 'city': 'Boston', 'country': 'US'}


WEATHER_DOC = {
    'ok': True, 'provider': 'openmeteo', 'city': 'Boston',
    'temp_f': 72, 'feels_like_f': 74, 'hi_f': 75, 'lo_f': 61,
    'sky': 'pcloudy', 'desc': 'Partly cloudy',
    'humidity': 47, 'wind_mph': 6,
    'forecast': [
        {'date': '2026-07-23', 'day': 'THU', 'hi_f': 75, 'lo_f': 61, 'sky': 'pcloudy', 'desc': 'Partly cloudy'},
        {'date': '2026-07-24', 'day': 'FRI', 'hi_f': 79, 'lo_f': 64, 'sky': 'clear', 'desc': 'Sunny'},
        {'date': '2026-07-25', 'day': 'SAT', 'hi_f': 71, 'lo_f': 60, 'sky': 'shwr', 'desc': 'Showers'},
    ],
}


def stub_get_weather(s=None, days=0, air=False):
    return dict(WEATHER_DOC)


HA_STATES = [
    {'entity_id': 'sensor.living_room_temp', 'state': '72.4',
     'attributes': {'friendly_name': 'Living Room', 'unit_of_measurement': '\N{DEGREE SIGN}F'}},
    {'entity_id': 'sensor.humidity', 'state': '47',
     'attributes': {'friendly_name': 'Humidity', 'unit_of_measurement': '%'}},
    {'entity_id': 'light.kitchen', 'state': 'on',
     'attributes': {'friendly_name': 'Kitchen'}},
    {'entity_id': 'binary_sensor.front_door', 'state': 'off',
     'attributes': {'friendly_name': 'Front Door'}},
    {'entity_id': 'sensor.office_co2', 'state': '612',
     'attributes': {'friendly_name': 'Office CO2', 'unit_of_measurement': 'ppm'}},
    {'entity_id': 'switch.espresso', 'state': 'on',
     'attributes': {'friendly_name': 'Espresso'}},
]


def stub_get_ha_states():
    return [dict(s) for s in HA_STATES]


HELPERS = {
    'i18n': None,
    'caps': None,
    'get_location': stub_get_location,
    'get_weather': stub_get_weather,
    'get_ha_states': stub_get_ha_states,
}


# Cap borrows the real Surface's PIL text toolkit (fit_font/wrap/message/text_card...)
# so migrated apps render through the exact production code paths.
def _borrow_surface_toolkit():
    from app.canvas import CanvasSurface as _CS
    for name in ('MIN_READABLE', 'fit_font', 'ink', 'wrap', 'wrap_fit', 'text_top',
                 'message', 'card_pages', '_card_header', 'text_card', 'mix', 'dim'):
        setattr(Cap, name, _CS.__dict__[name])


_borrow_surface_toolkit()


# ---- per-app settings overrides -------------------------------------------
GLOBALS = {
    'timezone': 'America/New_York',
    'temperature_unit': 'f',
    'currency_symbol': '$',
}

OVERRIDES = {
    'birdnet': {'birdnet_host': '10.0.0.42'},
    'calendar': {'ical_url': 'https://cal.example.com/family.ics'},
    'crypto': {'crypto_list': 'bitcoin,ethereum,solana'},
    'entity-board': {'config': (
        'sensor.living_room_temp | Living Room | 60,78\n'
        'sensor.humidity | Humidity\n'
        'light.kitchen | Kitchen\n'
        'binary_sensor.front_door | Front Door\n'
        'sensor.office_co2 | Office CO2 | 500,1000\n'
        'switch.espresso | Espresso')},
    'exchange-rates': {'base': 'USD'},
    'livestream': {
        'yt_channel_id': 'UCdemo123', 'yt_api_key': 'demo', 'yt_video_id': 'demo',
        'livestream_comments': ('Great stream tonight!\nLove from Boston\n\n'
                                'That solder joint was so clean\nDo the matrix build next!')},
    'sports': {'follows': 'mlb:BOS,nba:BOS'},
    'stocks': {'stocks_list': 'AAPL,MSFT,NVDA'},
    'time-since': {'event_name': 'LAUNCH DAY', 'event_date': '2025-11-08'},
    'youtube': {'yt_channel_id': 'UCdemo123', 'yt_api_key': 'demo'},
    'yt_comments': {'yt_api_key': 'demo'},
}


def settings_for(app_id):
    s = dict(GLOBALS)
    man = json.load(open(os.path.join(APPS, app_id, 'manifest.json')))
    for st in man.get('settings', []):
        k = st.get('key')
        if k:
            d = st.get('default')
            if k in GLOBALS and d in (None, ''):
                continue                      # a global (e.g. timezone) — keep the wall's value
            s[k] = '' if d is None else d
    s.update(OVERRIDES.get(app_id, {}))
    return s


# ---- per-app data-fetcher stubs -------------------------------------------
def _now_utc():
    return datetime.now(timezone.utc)


def stub_advice(m):
    m._fetch_advice = lambda: ("Don't be afraid to ask a question; "
                               "the answer might change everything.")


def stub_aurora(m):
    m._kp_data = lambda requests: (5.7, [2.3, 2.7, 3.0, 2.7, 3.3, 4.0, 4.7, 5.0, 5.3, 5.7])


def stub_birdnet(m):
    dets = [
        {'species': 'Northern Cardinal', 'confidence': 0.94, 'time': '07:42:12'},
        {'species': 'Black-capped Chickadee', 'confidence': 0.88, 'time': '07:38:05'},
        {'species': 'American Goldfinch', 'confidence': 0.79, 'time': '07:31:44'},
    ]
    m._recent_detections = lambda settings, limit: [dict(d) for d in dets]


def stub_fear_greed(m):
    m._index = lambda: (72, 'Greed')


def stub_calendar(m):
    def upcoming(settings, feeds, tz, pytz, now):
        ev1 = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if ev1 <= now:                        # already past 3:30 — keep the chip in the future
            ev1 = (now + timedelta(hours=2)).replace(minute=30, second=0, microsecond=0)
        ev2 = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        ev3 = (now + timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
        return [(ev1, False, 'Design review'),
                (ev2, False, 'Dentist appointment'),
                (ev3, True, "Maya's birthday")]
    m._fetch_feeds = lambda urls: ['stub-ical']
    m._upcoming = upcoming


def stub_cat_facts(m):
    m._fetch_fact = lambda settings: ('A group of cats is called a clowder, '
                                      'and a group of kittens is a kindle.')


def stub_chuck(m):
    m._fetch_fact = lambda: 'Chuck Norris counted to infinity. Twice.'


def stub_crypto(m):
    prices = {'bitcoin': {'usd': 117234.0, 'usd_24h_change': 2.4},
              'ethereum': {'usd': 4123.55, 'usd_24h_change': -1.2},
              'solana': {'usd': 212.34, 'usd_24h_change': 5.1}}
    m._prices = lambda coins, vs: prices


def stub_dog_facts(m):
    m._fetch_facts = lambda settings: (
        ["A dog's sense of smell is at least 40 times better than ours."], 250)


def stub_earthquakes(m):
    now_ms = _now_utc().timestamp() * 1000
    feats = [
        {'properties': {'mag': 6.1, 'place': '42 km SSW of Adak, Alaska',
                        'time': now_ms - 35 * 60 * 1000}},
        {'properties': {'mag': 5.4, 'place': '18 km E of Kokopo, Papua New Guinea',
                        'time': now_ms - 3 * 3600 * 1000}},
        {'properties': {'mag': 4.8, 'place': '9 km NW of Coquimbo, Chile',
                        'time': now_ms - 6 * 3600 * 1000}},
    ]
    m._quakes = lambda minmag, limit=5: feats


def stub_exchange_rates(m):
    m._rates = lambda base, targets: {'EUR': 0.8624, 'GBP': 0.7412, 'JPY': 147.52}


def stub_formula1(m):
    race_day = (_now_utc() + timedelta(days=4)).strftime('%Y-%m-%d')
    m._next_race = lambda: {'raceName': 'Belgian Grand Prix', 'round': '13',
                            'date': race_day, 'time': '13:00:00Z'}
    m._driver_standings = lambda: [{'Driver': {'familyName': 'Norris'}, 'points': '255'}]


def stub_iss(m):
    m._iss_position = lambda: {'iss_position': {'latitude': '38.72', 'longitude': '-27.14'}}
    m._astros = lambda: {'number': 7}


def stub_livestream(m):
    m._channel_title = lambda cid: 'Workshop Live'
    m._live_viewers = lambda key, vid: 1284


def stub_metals(m):
    m._local_currency = lambda i18n, get_location: ('USD', 1.0)
    m._spot_price = lambda sym: 3358.40 if sym == 'XAU' else 38.20


def stub_metro(m):
    m._next_arrivals = lambda stop, route: {0: 3, 1: 7}
    m._destinations = lambda route: {0: 'Forest Hills', 1: 'Oak Grove'}


def stub_news(m):
    m._headlines = lambda feed_url: [
        'Ceasefire talks resume as leaders meet in Cairo',
        'Markets rally after surprise rate cut',
        'Webb telescope spots water on distant exoplanet',
        'England seal series win in final-over thriller',
        'EU agrees landmark AI transparency rules',
        'Rare aurora dazzles skies across northern Europe',
    ]


def stub_on_this_day(m):
    m._events = lambda settings: [
        ('1969', 'Apollo 11 astronauts return safely to Earth after the first Moon landing'),
        ('1904', 'The ice cream cone is popularized at the St. Louis World\'s Fair'),
        ('1962', 'Telstar relays the first live transatlantic television signal'),
    ]


def stub_planes(m):
    now = int(_now_utc().timestamp())
    flights = [
        {'callsign': 'DAL212', 'lat': 42.29, 'lon': -70.91, 'altitude_m': 3200,
         'speed_ms': 180.0, 'origin': 'BOS', 'destination': 'ATL',
         'on_ground': False, 'last_seen': now},
        {'callsign': 'JBU847', 'lat': 42.47, 'lon': -71.18, 'altitude_m': 5500,
         'speed_ms': 210.0, 'origin': 'BOS', 'destination': 'FLL',
         'on_ground': False, 'last_seen': now},
        {'callsign': 'UAL33', 'lat': 42.21, 'lon': -71.30, 'altitude_m': 10100,
         'speed_ms': 245.0, 'origin': 'EWR', 'destination': 'LHR',
         'on_ground': False, 'last_seen': now},
    ]
    m._shared_flights = lambda settings, get_location: (flights, (42.36, -71.05), 46.3, None)
    m.fetch._state = {'last_sig': None, 'last_polled_at': 0.0, 'flights': flights,
                      'last_error_provider': None, 'last_error': None,
                      'opensky_token': None, 'opensky_token_exp': 0.0}


def stub_quote(m):
    m._best_quote = lambda settings: ('The best way to predict the future is to invent it.',
                                      'Alan Kay')


def stub_rocket(m):
    net = (_now_utc() + timedelta(hours=26, minutes=14)).strftime('%Y-%m-%dT%H:%M:%SZ')
    m._next_launch = lambda: {'name': 'Falcon 9 Block 5 | Starlink Group 12-40', 'net': net}


def stub_chomper(m):
    # A fresh game's first frame is an untouched maze — pre-run the sim a dozen
    # ticks so the screenshot shows a game in progress (deterministic: the game's
    # rng is seeded from level+step).
    orig = m._state

    def warmed(cols, rows, n_ghosts):
        st = orig(cols, rows, n_ghosts)
        if st['step'] == 0:
            for _ in range(12):
                m._step(st)
        return st
    m._state = warmed


def stub_sports(m):
    raw = [
        ('MLB', 'BOT 7', 'in', ('NYY', '4'), ('BOS', '5'), 'NYY 4  BOS 5'),
        ('NBA', '7:30 PM', 'pre', ('BOS', ''), ('NYK', ''), 'BOS vs NYK'),
    ]

    def gather(settings, format_lines, get_cols):
        fmt = format_lines if callable(format_lines) else (lambda *ls: '\n'.join(ls))
        return [{'league': lg, 'status': st, 'state': gs, 'away': aw, 'home': hm,
                 'score_line': sl, 'page': fmt(lg, sl, st)} for lg, st, gs, aw, hm, sl in raw]
    m._gather_games = gather


def stub_stocks(m):
    quotes = {'AAPL': (232.11, 229.45, 'USD', 'America/New_York'),
              'MSFT': (521.68, 524.10, 'USD', 'America/New_York'),
              'NVDA': (181.42, 176.90, 'USD', 'America/New_York')}
    m._quote = lambda sym: quotes[sym]


def stub_sun_times(m):
    # Pin the location's local clock to ~15:30 so the sun rides high on a lit arc.
    utc = _now_utc().replace(tzinfo=None)
    desired_local = utc.replace(hour=15, minute=30, second=0, microsecond=0)
    offset = int(round((desired_local - utc).total_seconds() / 900.0) * 900)
    day = (utc + timedelta(seconds=offset)).strftime('%Y-%m-%d')
    m._sun_data = lambda settings, requests, get_location: {
        'sunrise': f'{day}T05:42', 'sunset': f'{day}T20:19', 'daylight': 52620}
    m._cached_sun = lambda settings, get_location: {
        'sunrise': f'{day}T05:42', 'sunset': f'{day}T20:19',
        'daylight': 52620, 'utc_offset': offset}


def stub_tides(m):
    day = datetime.now().strftime('%Y-%m-%d')
    m._predictions = lambda station: [
        {'t': f'{day} 04:12', 'type': 'L', 'v': '0.4'},
        {'t': f'{day} 10:28', 'type': 'H', 'v': '9.8'},
        {'t': f'{day} 16:41', 'type': 'L', 'v': '0.9'},
        {'t': f'{day} 22:54', 'type': 'H', 'v': '10.6'},
    ]


def stub_trivia(m):
    m._fetch_qa = lambda: ('Which planet has the most moons?',
                           'Saturn - 146 confirmed moons')


def stub_useless_fact(m):
    m._best_fact = lambda settings, i18n=None: (
        'Honey never spoils - edible honey has been found in 3,000-year-old Egyptian tombs.')


def stub_wiki(m):
    m._feed = lambda settings, i18n=None: (
        'Voyager 1',
        ['Aurora borealis', 'Tour de France', 'Alan Turing', 'Perseid meteor shower',
         "Halley's Comet"])


def stub_youtube(m):
    m._channel_feed = lambda cid: ('The Split Flap Shop',
                                   ['Building a 4-panel LED matrix wall',
                                    'Split-flap clock teardown'])
    m._subscriber_count = lambda cid, key: 128000


def stub_yt_comments(m):
    m._comments = lambda settings: [
        ('@makerdan', 'This is exactly the kind of over-engineering I subscribe for. '
                      'The flap sound at 2:14 is so satisfying.'),
        ('@el_gato', 'Do a matrix panel version next!'),
    ]


STUBS = {
    'advice': stub_advice,
    'aurora': stub_aurora,
    'birdnet': stub_birdnet,
    'bitcoin-fear-greed': stub_fear_greed,
    'calendar': stub_calendar,
    'cat-facts': stub_cat_facts,
    'chuck-norris': stub_chuck,
    'crypto': stub_crypto,
    'dog-facts': stub_dog_facts,
    'earthquakes': stub_earthquakes,
    'exchange-rates': stub_exchange_rates,
    'formula1': stub_formula1,
    'iss': stub_iss,
    'livestream': stub_livestream,
    'metals': stub_metals,
    'metro': stub_metro,
    'news-headlines': stub_news,
    'on-this-day': stub_on_this_day,
    'planes_overhead': stub_planes,
    'quote': stub_quote,
    'rocket-launch': stub_rocket,
    'canvas-chomper': stub_chomper,
    'sports': stub_sports,
    'stocks': stub_stocks,
    'sun-times': stub_sun_times,
    'tides': stub_tides,
    'trivia': stub_trivia,
    'useless-fact': stub_useless_fact,
    'wiki-today': stub_wiki,
    'youtube': stub_youtube,
    'yt_comments': stub_yt_comments,
}


# ---- render loop -----------------------------------------------------------
def load_app(app_id):
    path = os.path.join(APPS, app_id, 'app.py')
    spec = importlib.util.spec_from_file_location(f'harness_{app_id.replace("-", "_")}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clear_states(mod):
    for v in vars(mod).values():
        if callable(v) and hasattr(v, '__dict__'):
            v.__dict__.pop('_state', None)
            v.__dict__.pop('_st', None)


def helper_kwargs(fn):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return dict(HELPERS)
    return {n: v for n, v in HELPERS.items() if n in params}


def render(app_id, w, h):
    if app_id in CHANNELS:
        from app import channel_art
        motif, text = CHANNELS[app_id]
        cap = Cap(w, h)
        # The engine splits a too-long screen into readable pages; show page 1.
        channel_art.render(cap, channel_art.fit_pages(cap, text, motif)[0], motif)
        return cap.frames[-1]
    mod = load_app(app_id)
    clear_states(mod)
    stub = STUBS.get(app_id)
    if stub:
        stub(mod)
    cap = Cap(w, h)
    settings = settings_for(app_id)
    kwargs = helper_kwargs(mod.fetch_matrix)
    mod.fetch_matrix(settings, cap, **kwargs)
    if not cap.frames:
        raise RuntimeError('fetch_matrix pushed no frame')
    return cap.frames[-1]


# ---- split-flap wall mockups (15 cols; 2/3/5-row walls) ---------------------
FLAP_COLS = 15
FLAP_WALLS = [3, 5, 2]                # rows: the classic 3-high, a tall 5, a slim 2
FLAP_COLORS = {'r': (217, 58, 43), 'o': (232, 132, 44), 'y': (232, 198, 44),
               'g': (63, 174, 74), 'b': (47, 111, 224), 'p': (142, 79, 208),
               'w': (232, 232, 234)}


def app_names():
    """App id -> display name, from the manifests — sheet labels show the name users see."""
    names = {}
    for aid in os.listdir(APPS):
        mp = os.path.join(APPS, aid, 'manifest.json')
        if os.path.exists(mp):
            m = json.load(open(mp))
            names[m['id']] = m.get('name', m['id'])
    return names


def flap_apps():
    """Every app with a flap surface, from the manifests (channels included)."""
    out = []
    for aid in sorted(os.listdir(APPS)):
        mp = os.path.join(APPS, aid, 'manifest.json')
        if not os.path.exists(mp):
            continue
        m = json.load(open(mp))
        # Animations are skipped: a static screenshot of a motion effect is meaningless.
        if bool(m.get('animation')) or m['id'].startswith('anim_'):
            continue
        if 'flap' in m.get('surfaces', ['flap']):
            out.append((m['id'], False, m.get('type') in ('channel', 'quiz')))
    return out


def _make_format_lines(nrows):
    def format_lines(*lines, cols=FLAP_COLS, align='center'):
        given = [str(ln)[:cols].center(cols) for ln in list(lines)[:nrows]]
        pad = nrows - len(given)
        top = 0 if align == 'top' else (pad if align == 'bottom' else pad // 2)
        rows = [' ' * cols] * top + given + [' ' * cols] * (pad - top)
        return '\n'.join(rows[:nrows])
    return format_lines


def _flap_wrap(text, cols=FLAP_COLS):
    lines, cur = [], ''
    text = str(text).replace('\u2014', '-').replace('\u2013', '-')
    for wd in text.replace('\n', ' ').split():
        cand = (cur + ' ' + wd).strip()
        if len(cand) <= cols:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = wd[:cols]
    if cur:
        lines.append(cur)
    return lines


def _make_paginate(nrows):
    from app import textlayout
    fmt = _make_format_lines(nrows)
    return lambda text, title='': [fmt(*page) for page in
                                   textlayout.balanced_pages(text, nrows, FLAP_COLS, title)]


def flap_page(app_id, is_anim, is_channel, nrows):
    """The app's FIRST flap page as ``nrows`` strings of FLAP_COLS chars."""
    fmt = _make_format_lines(nrows)
    if is_channel:
        rows = fmt(*_flap_wrap(CHANNELS[app_id][1])).split('\n')
    else:
        mod = load_app(app_id)
        clear_states(mod)
        stub = STUBS.get(app_id)
        if stub:
            stub(mod)
        kwargs = helper_kwargs(mod.fetch)
        if 'paginate' in inspect.signature(mod.fetch).parameters:
            kwargs['paginate'] = _make_paginate(nrows)   # the wall-bound wrap/paginate helper
        pages = mod.fetch(settings_for(app_id), fmt,
                          lambda: nrows, lambda: FLAP_COLS, **kwargs)
        if not isinstance(pages, list):
            pages = [str(pages)]
        page = pages[0]
        text = page if isinstance(page, str) else str(page.get('text', ''))
        rows = text.split('\n') if '\n' in text else \
            [text[i:i + FLAP_COLS] for i in range(0, max(1, len(text)), FLAP_COLS)]
    out = []
    for r in range(nrows):
        ln = rows[r] if r < len(rows) else ''
        out.append(ln[:FLAP_COLS].ljust(FLAP_COLS) if is_anim
                   else ln[:FLAP_COLS].upper().ljust(FLAP_COLS))
    return out


def draw_flap_wall(rows):
    """A split-flap wall mockup (len(rows) x FLAP_COLS): rounded dark modules, a split
    line, bold glyphs — and color flaps (emoji tiles) as solid modules."""
    MW, MH, GAP, MARGIN = 28, 38, 4, 12
    W = MARGIN * 2 + FLAP_COLS * MW + (FLAP_COLS - 1) * GAP
    H = MARGIN * 2 + len(rows) * MH + (len(rows) - 1) * GAP
    img = Image.new('RGB', (W, H), (13, 14, 16))
    d = ImageDraw.Draw(img)
    glyph = ImageFont.truetype(os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'), 26)
    from app import renderer as _renderer
    bbx = d.textbbox((0, 0), 'X', font=glyph)
    gy0 = (MH - (bbx[3] - bbx[1])) // 2 - bbx[1]   # shared baseline: cap box centered
    for ry, line in enumerate(rows):
        for cx, ch in enumerate(line):
            x = MARGIN + cx * (MW + GAP)
            y = MARGIN + ry * (MH + GAP)
            code = _renderer.COLOR_MAP.get(ch) or _renderer.PUA_TO_CODE.get(ch) or ch
            color = FLAP_COLORS.get(code)
            if color is None and (ord(ch[:1] or ' ') > 126):
                ch = ' '                            # no flap for this glyph: blank module
            if color is not None:
                top = color
                bottom = tuple(max(0, int(c * 0.78)) for c in color)
            else:
                top, bottom = (37, 40, 45), (28, 30, 34)
            d.rounded_rectangle([x, y, x + MW - 1, y + MH - 1], radius=3, fill=bottom)
            d.rounded_rectangle([x, y, x + MW - 1, y + MH // 2 - 1], radius=3, fill=top)
            d.rectangle([x, y + MH // 2 - 2, x + MW - 1, y + MH // 2 - 1], fill=top)
            if color is None and ch not in (' ', ''):
                gx = x + (MW - d.textlength(ch, font=glyph)) // 2
                d.text((gx, y + gy0), ch, font=glyph, fill=(238, 239, 242))
            d.line([x + 1, y + MH // 2 - 1, x + MW - 2, y + MH // 2 - 1],
                   fill=(10, 10, 12), width=1)
    return img


def main():
    # With app ids on the command line, only those are re-rendered; every other
    # tile is loaded back from review/r<res>/ (saved 4x NEAREST, so /4 NEAREST
    # recovers the exact pixels) so the contact sheets keep the full grid.
    flap_set = flap_apps()
    flap_ids = [a for a, _, _ in flap_set]
    only = [a for a in sys.argv[1:] if a in APP_IDS or a in flap_ids]
    results, failures = {}, []
    for w, h in RESOLUTIONS:
        outdir = os.path.join(REVIEW, f'r{w}x{h}')
        os.makedirs(outdir, exist_ok=True)
        for app_id in APP_IDS:
            if only and app_id not in only:
                p = os.path.join(outdir, f'{app_id}.png')
                if os.path.exists(p):
                    results[(app_id, w, h)] = Image.open(p).convert('RGB').resize(
                        (w, h), Image.NEAREST)
                continue
            try:
                img = render(app_id, w, h)
            except Exception as e:
                failures.append((app_id, f'{w}x{h}', repr(e)))
                traceback.print_exc()
                continue
            results[(app_id, w, h)] = img
            img.resize((w * 4, h * 4), Image.NEAREST).save(
                os.path.join(outdir, f'{app_id}.png'))
        n_want = len(only) if only else len(APP_IDS)
        n_got = sum(1 for k in results if k[1] == w and k[2] == h and (not only or k[0] in only))
        print(f'-- {w}x{h}: {n_got}/{n_want} rendered')

    # ---- contact sheets ---------------------------------------------------
    label_font = ImageFont.truetype(os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'), 22)
    names = app_names()
    TILE_W = 512
    COLS = 4
    PAD = 24
    LABEL_H = 34
    BG = (32, 34, 38)
    FG = (225, 227, 232)

    def build_sheet(w, h, out_path):
        tile_h = int(TILE_W * h / w)
        apps = [a for a in APP_IDS if (a, w, h) in results]
        rows = (len(apps) + COLS - 1) // COLS
        sheet_w = COLS * TILE_W + (COLS + 1) * PAD
        sheet_h = rows * (LABEL_H + tile_h + PAD) + PAD
        sheet = Image.new('RGB', (sheet_w, sheet_h), BG)
        d = ImageDraw.Draw(sheet)
        for i, app_id in enumerate(apps):
            r, c = divmod(i, COLS)
            x = PAD + c * (TILE_W + PAD)
            y = PAD + r * (LABEL_H + tile_h + PAD)
            d.text((x + 2, y + 2), names.get(app_id, app_id), font=label_font, fill=FG)
            tile = results[(app_id, w, h)].resize((TILE_W, tile_h), Image.NEAREST)
            sheet.paste(tile, (x, y + LABEL_H))
            d.rectangle([x - 1, y + LABEL_H - 1, x + TILE_W, y + LABEL_H + tile_h],
                        outline=(58, 61, 68))
        sheet.save(out_path)
        print('sheet:', out_path, f'{sheet_w}x{sheet_h}')

    for w, h in RESOLUTIONS:
        build_sheet(w, h, os.path.join(REVIEW, f'contact-sheet-{w}x{h}.png'))

    # ---- split-flap wall mockups ------------------------------------------
    for nrows in FLAP_WALLS:
        flap_dir = os.path.join(REVIEW, f'flap-{nrows}x{FLAP_COLS}')
        os.makedirs(flap_dir, exist_ok=True)
        flap_results = {}
        for app_id, is_anim, is_channel in flap_set:
            out_p = os.path.join(flap_dir, f'{app_id}.png')
            if only and app_id not in only:
                if os.path.exists(out_p):
                    flap_results[app_id] = Image.open(out_p).convert('RGB')
                continue
            try:
                img = draw_flap_wall(flap_page(app_id, is_anim, is_channel, nrows))
            except Exception as e:
                failures.append((app_id, f'flap-{nrows}x{FLAP_COLS}', repr(e)))
                traceback.print_exc()
                continue
            flap_results[app_id] = img
            img.save(out_p)
        print(f'-- flap {nrows}x{FLAP_COLS}: {len(flap_results)}/{len(flap_set)} present')

        fl = [a for a, _, _ in flap_set if a in flap_results]
        if fl:
            fw, fh = next(iter(flap_results.values())).size
            tile_h = int(TILE_W * fh / fw)
            rows = (len(fl) + COLS - 1) // COLS
            sheet_w = COLS * TILE_W + (COLS + 1) * PAD
            sheet_h = rows * (LABEL_H + tile_h + PAD) + PAD
            sheet = Image.new('RGB', (sheet_w, sheet_h), BG)
            d = ImageDraw.Draw(sheet)
            for i, app_id in enumerate(fl):
                r, c = divmod(i, COLS)
                x = PAD + c * (TILE_W + PAD)
                y = PAD + r * (LABEL_H + tile_h + PAD)
                d.text((x + 2, y + 2), names.get(app_id, app_id), font=label_font, fill=FG)
                sheet.paste(flap_results[app_id].resize((TILE_W, tile_h), Image.LANCZOS),
                            (x, y + LABEL_H))
                d.rectangle([x - 1, y + LABEL_H - 1, x + TILE_W, y + LABEL_H + tile_h],
                            outline=(58, 61, 68))
            out_path = os.path.join(REVIEW, f'contact-sheet-flap-{nrows}x{FLAP_COLS}.png')
            sheet.save(out_path)
            print('sheet:', out_path, f'{sheet_w}x{sheet_h}')

    print()
    if failures:
        print('FAILURES:')
        for f in failures:
            print('  ', *f)
    else:
        print('No failures.')


if __name__ == '__main__':
    main()
