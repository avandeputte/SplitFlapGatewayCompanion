# App audit — July 2026

> **Status (2.5.0-beta.1):** §E executed in full — platform gaps A1/A2/A4, the
> whole bug sweep (B), quick wins C3-C8, C1, the C9 translations (40 files), and
> doctrine D1-D5 (now the Writing-Apps "house rules", enforced by
> `test_app_conformance.py` / `test_injected_helpers.py`). Deliberately left:
> A3 (`skip_rotation_wait` — wire or delete, engine work), A5 (refresh
> multiplier), C2's per-country holiday *data* (the i18n mechanism landed; the
> data would have to be curated, not invented), and D6-D10.

Every app (64) audited for unexploited platform capabilities and structural drift:
a mechanical scan of all manifests/signatures, then six parallel deep reviews
(clocks, weather/nature, sky/markets, sports/news, quotes/channels, media/anims)
with findings verified against the actual renderer/engine/injection code. This
file is the consolidated result — a work list, ordered by what it buys.

Legend: **S/M/L** = effort. `file:line` refs are as of this audit.

---

## A. Platform gaps (fix once, unlock many apps)

The audit's biggest finding is that the worst app-level duplication is *forced*
by missing platform surface:

1. **No injected way to get coordinates.** `get_location()` returns
   country/subdivision/currency — no lat/lon. That is why sun-times, forecast-ribbon
   and weather each carry the same geocode ladder (settings → Nominatim
   `countrycodes=us` → Boston), two of them as sanctioned fallbacks, sun-times as
   its only path. Fix: extend `get_location()` with `lat/lon/city` (it already
   sits on the cached `location.coordinates()`), or extend `get_weather` to carry
   daily sunrise/sunset so sun-times needs no second source. **L, platform.**

2. **Triggers are capability-starved.** `trigger(settings, conditions)` gets no
   injected helpers at all, so iss hand-rolls Nominatim + Open-Meteo + pytz *inside
   its trigger* out of necessity, and every trigger copies tz boilerplate. Fix:
   inject the same opt-in kwargs into `trigger()` as `fetch()`. **M, platform.**

3. **`skip_rotation_wait` is parsed but never consumed** for functional apps —
   plugins.py:750 produces the page flag; no rotation loop reads it. Wire it into
   the engine or delete it; until then no app should be told to add it. **M, platform.**

4. **No timezone helper.** ~18 apps copy `pytz.timezone(settings.get('timezone',
   'US/Eastern'))`; the guard is present in ~10, missing in art-clock, time,
   time-since (fetch+trigger), countdown-trigger, national-today (crash path),
   moon-phase-trigger; the fallback target diverges (calendar → UTC, rest →
   US/Eastern) — and the fallback is near-dead anyway because the host tz is
   injected into settings. Fix: a tz helper on `i18n` (e.g. `i18n.tz(settings)`)
   or a blessed guarded snippet in Writing-Apps; standardize the fallback (UTC).
   **M platform / S doctrine.**

5. **`refresh_interval` cannot adapt to the wall.** sports (15 s) and metro (30 s)
   cadences are only honest on an `instant` wall, but refresh is static manifest
   config. The engine's unchanged-page suppression (engine.py:376) already blunts
   the reel cost; a non-instant refresh multiplier in the engine would finish the
   job. **M, platform, optional.**

## B. Bugs — fix regardless of any standardization

| App | Bug | Effort |
|---|---|---|
| yt_comments | Renders `textDisplay` — HTML entities/tags (`&#39;`, `<br>`) land on the flaps. Use `textOriginal`. Also declares no min sizes; on 1-row walls drops the comment entirely | S |
| news-headlines, on-this-day, trivia | Copy-pasted ASCII allow-set strips accents/`€` to spaces **before** the renderer's wall-aware degradation (which would have kept `É` on capable reels). Delete the filter — case is already handled by testing `c.upper()` (news:46/53, otd:15/22, trivia:57-59) | S ×3 |
| time-since | Ticking seconds with **no `caps.instant` gate** (the permanent-clatter case) and ignores `get_rows/get_cols` entirely; missing tz guards in fetch+trigger | M |
| sarcastic-fortune-cookies | Cached rendered page not invalidated on wall resize — stale layout until `frequency` elapses (app.py:141-152). Add rows/cols to the state key | M |
| bitcoin-fear-greed | No min_rows/min_cols; passes 3 fixed lines and ignores geometry — on 1-2-row walls **its own index value is silently dropped** | M |
| crypto | `c[:6].upper()` uppercases+truncates the CoinGecko id slug → "BITCOI"; 3-row layout shows the same coin lowercase — inconsistent naming | S |
| stocks / crypto / iss / metals | min_cols under-declared vs real layout (stocks `8` makes the ticker vanish under `_row`; crypto 8→~13; iss 8 vs 28-char raw-coordinate line — also apply `trim()` in 1/2-row branches; metals 12→13) | S each |
| all 12 channels | `min_cols: 10` but every data file is authored 15 wide — guaranteed truncation on 10-14-col walls. Set 15 | S ×12 |
| art-clock | `min_cols: 12` vs its own fixed 15-wide layout; missing tz guard; no timezone setting declared | S |
| moon-phase | Timezone code that cancels itself out (`now(tz)` → `astimezone(utc)`) in fetch and trigger; trigger unguarded. Use `datetime.now(timezone.utc)` | S |
| birdnet | `refresh_interval: 1` hammers the BirdNET-Pi every second (~3 s analysis windows; 5-10 s is plenty); default host is a private LAN IP (`192.168.86.139`) shipped as product default | S |
| livestream | `livestream_interval` setting is never read (dead); channel name pre-truncated `[:15]` regardless of `get_cols()` | S |
| metro | Reads `disable_colors` it never declares; manifest/code default stop mismatch (`place-NSTAT` vs `place-bbsta`) | S |
| youtube | Name/description say subscriber counter; the RSS feed yields a **video** count. Rename or fetch real subs; trigger silently depends on undeclared `yt_api_key` | S |
| sun-times / earthquakes / rocket-launch | `min_rows: 2` with real (unreachable) 1-row layouts — under-advertise or drop the dead branch | S |
| national-today | Unguarded tz (crash path); `get_cols()` called inside the per-name loop; wrapper silently drops words that miss line 2 | S |

## C. Capability adoption — ranked wins

1. **useless-fact** hardcodes `language: 'en'`; the API supports more. Add
   `i18n=None`, map `i18n.lang` — near-free localization (the group's reference
   is sarcastic-fortune-cookies' read-`.lang`, load-`data_<lang>`, fallback-en). **M**
2. **national-today**: `i18n.country()` and `i18n.holiday(name)` exist *for this
   app* and it uses neither; data is US-only. Country-aware calendars + localized
   names. **L (data)**
3. **sun-times**: until platform gap A1 lands, ride `get_weather()` for lat/lon
   (every doc carries them) and keep only the daily sunrise call. **M**
4. **countdown**: replace the `__main__.FLAP_CHARS` reach-in with
   `caps.can_show(ch)` — the one place caps is the right tool and is bypassed. **M**
5. **livestream**: `i18n.number` for viewer counts (hardcoded `:,` grouping),
   `i18n.time` for the 12h-only clock. **S**
6. **metro**: localize `min`/`Dir0/Dir1`/errors via i18n. **S**
7. **Tall-wall utilization**: on-this-day shows one event and discards the rest;
   formula1 shows only the championship leader; date/time leave a guaranteed
   blank row at rows≥4. Concrete "show more" targets. **S-M each**
8. **Severity colour tiles** (universal squares, no caps needed): aurora Kp,
   earthquakes magnitude, bitcoin-fear-greed sentiment; moon-phase's illumination
   bar should use 🟨/⬛ tiles instead of the literal letter `w`. **S each**
9. **Channel localization gaps**: magic-8-ball, fortune-cookie, stoic-quotes,
   shower-thoughts are generic + translatable but ship en-only next to localized
   siblings (dad-jokes, good-morning/night, motivational have 10 languages). **M data each**
10. **earthquakes**: only weather-group data app with zero i18n (`m ago`, `h ago`). **M**

**Explicitly rejected** (verified against the platform): caps-gating the stocks/
crypto arrows (the renderer already degrades `↑`→`^` on plain reels — emitting
them unconditionally is correct); `caps.lowercase` for yt_comments (passthrough
text already renders real-case on drawn walls via downstream folding); baked
pictographs in channel data files (no can_show fallback possible — they'd blank).

## D. Standardization doctrine (bless in Writing-Apps; enforce where cheap)

1. **One truthy parser** — whitelist `in {'1','true','yes','on'}` (binary-clock's
   blacklist variant is the outlier).
2. **One guarded tz snippet**, UTC fallback, documented once.
3. **Error convention**: prefer *raise* (engine shows OFFLINE/cached); hand-built
   error pages must go through `t()`; bless the last-good-pages pattern (weather,
   birdnet) for network apps — the five new fact apps regressed to dead "Offline"
   pages where chuck-norris/trivia fall back to canned content (better UX).
4. **min sizing is a contract**: min_rows/min_cols must match the widest/tallest
   line actually produced. (Candidate test: render each app's error-free path at
   its declared minimum and assert nothing truncates.)
5. **i18n badge bar**: manifest `i18n: true` ⇔ fetch accepts `i18n` and localizes
   visible chrome. (Candidate test: flag implies param.) forecast-ribbon carries a
   dead i18n param; exchange-rates/bitcoin-fear-greed fly the badge on partial
   localization.
6. **One wrap/paginate implementation**: the five fact apps carry three drifted
   variants of the same `_pages`; cat-facts/dog-facts is the canonical one. Re-sync
   all five; drop the baked `Advice:`/`Quote:` prefixes (title-row machinery
   exists); unify the three refresh-setting conventions (`refresh_minutes` vs none
   vs `frequency`).
7. **Animation canon**: pure function of grid, colour codes on animation:true
   pages, settings 4 / 0.1 / 10 / 0.1. anim_sweep deviates on min/step (0.05);
   anim_random_spin diverges structurally (module-global state shared across
   walls — use `fetch._state`; manifest default 4 vs code fallback 0.5; dwell-vs-
   speed semantics need reconciling).
8. **Colour = emoji squares** in normal pages everywhere (art-clock's letter-code
   + animation:true approach is the outlier; migrating changes its aesthetic —
   optional).
9. **Dead `rows==1` branches** under `min_rows: 2` (calendar, holidays,
   rocket-launch, sun-times, earthquakes): pick per app — lower the min or drop
   the branch.
10. **Reference apps** to point Writing-Apps at: calendar, word-clock, tides
    (caps done right), wiki-today, word-of-the-day, rocket-launch, weather.

## E. Suggested execution order

1. Bug sweep (B) — nearly all S; the accent-filter deletions, yt_comments,
   min_cols corrections, and time-since gating are user-visible today.
2. Doctrine + Writing-Apps updates (D1-D5) with the two cheap conformance tests.
3. Quick capability wins (C4-C8).
4. Platform gaps (A1, A2, A4) — then the sun-times/iss cleanups they unlock.
5. Data projects (C1, C2, C9) as content work.
