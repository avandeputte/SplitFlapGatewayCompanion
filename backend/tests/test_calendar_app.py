"""The calendar app: parsing iCal, and saying what is next.

The fixture here is SYNTHETIC. A real iCal URL is a credential — anyone holding it can read
the calendar — so no real feed, and no real event, is ever committed to this repo.

Everything below is a thing an actual Google feed does, and each one has a way of going wrong
quietly:

* a folded line (RFC 5545 wraps at 75 octets and continues with a leading space);
* a VALARM nested inside the VEVENT, carrying its own UID;
* a VTIMEZONE, carrying its own DTSTART — which a naive scan happily reads as the meeting's,
  putting your 9am in 1970;
* all-day events (VALUE=DATE), UTC instants (…Z), and wall-clock times in a named zone;
* an RRULE, because the next event on most people's calendar is a recurring one;
* EXDATE, and STATUS:CANCELLED.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz

from conftest import load_app, make_runtime

cal = load_app("calendar")
TZ = pytz.timezone("US/Eastern")


def _ics(*vevents: str) -> str:
    """A feed with a VTIMEZONE in front of it, exactly as Google sends one."""
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "BEGIN:VTIMEZONE",
        "TZID:America/New_York",
        "BEGIN:DAYLIGHT",
        "DTSTART:19700308T020000",          # <- the trap: not an event
        "TZOFFSETFROM:-0500",
        "TZOFFSETTO:-0400",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
        *vevents,
        "END:VCALENDAR",
    ])


def _vevent(*lines: str) -> str:
    return "\r\n".join(["BEGIN:VEVENT", *lines, "END:VEVENT"])


def _parse(text):
    return cal._events(text, TZ, pytz)


# --- parsing ----------------------------------------------------------------

def test_a_timezone_definition_is_not_an_event():
    """VTIMEZONE carries a DTSTART of its own. Reading it would invent a 1970 meeting."""
    evs = _parse(_ics(_vevent("DTSTART:20260716T130000Z", "SUMMARY:Standup")))
    assert len(evs) == 1
    assert evs[0]["summary"] == "Standup"
    assert evs[0]["start"].year == 2026


def test_an_alarm_inside_the_event_is_not_a_second_event():
    """A VALARM sits INSIDE the VEVENT and has its own UID. It is not an event."""
    evs = _parse(_ics(_vevent(
        "DTSTART:20260716T130000Z",
        "SUMMARY:Dentist",
        "BEGIN:VALARM",
        "ACTION:AUDIO",
        "UID:the-alarms-own-uid",
        "TRIGGER:-PT15H",
        "END:VALARM",
    )))
    assert len(evs) == 1 and evs[0]["summary"] == "Dentist"


def test_a_folded_line_is_one_value():
    """RFC 5545 wraps at 75 octets and continues with a leading space, which unfolding removes.

    The fold lands wherever the 75th octet does — usually MID-WORD — and the space that begins
    the continuation is the folder's, not part of the text. So the two halves are concatenated
    with nothing between them: "whole te" + " am and…" is "whole team and…". Get this wrong by
    keeping the space and you corrupt every long title in the feed; get it wrong by splitting
    on the space and you truncate them.
    """
    evs = _parse(_ics(_vevent(
        "DTSTART:20260716T130000Z",
        "SUMMARY:Quarterly planning with the whole te",
        " am and a very long title indeed",
    )))
    assert evs[0]["summary"] == "Quarterly planning with the whole team and a very long title indeed"


def test_the_three_shapes_of_dtstart():
    evs = _parse(_ics(
        _vevent("DTSTART;VALUE=DATE:20260716", "SUMMARY:Birthday"),
        _vevent("DTSTART:20260716T130000Z", "SUMMARY:In UTC"),
        _vevent("DTSTART;TZID=America/New_York:20260716T090000", "SUMMARY:Wall clock"),
    ))
    by = {e["summary"]: e for e in evs}
    assert by["Birthday"]["all_day"] is True
    # 13:00 UTC is 09:00 in New York — the app renders in the user's zone, not the feed's.
    assert by["In UTC"]["all_day"] is False
    assert by["In UTC"]["start"].hour == 9
    assert by["Wall clock"]["start"].hour == 9


def test_escapes_are_unescaped():
    evs = _parse(_ics(_vevent(
        "DTSTART:20260716T130000Z",
        r"SUMMARY:Lunch\, then a walk\; maybe",
    )))
    assert evs[0]["summary"] == "Lunch, then a walk; maybe"


def test_a_cancelled_event_is_not_shown():
    evs = _parse(_ics(
        _vevent("DTSTART:20260716T130000Z", "SUMMARY:Called off", "STATUS:CANCELLED"),
        _vevent("DTSTART:20260716T140000Z", "SUMMARY:Still on", "STATUS:CONFIRMED"),
    ))
    assert [e["summary"] for e in evs] == ["Still on"]


# --- recurrence -------------------------------------------------------------

def _next(ev, now, days=60):
    return cal._next_occurrence(ev, now, now + timedelta(days=days))


def test_a_weekly_event_recurs():
    """The next thing on most calendars IS the recurring one — a standup, a bin collection."""
    ev = _parse(_ics(_vevent(
        "DTSTART;TZID=America/New_York:20260106T090000",   # a Tuesday, months ago
        "RRULE:FREQ=WEEKLY;BYDAY=TU",
        "SUMMARY:Standup",
    )))[0]
    now = TZ.localize(datetime(2026, 7, 15, 12, 0))        # a Wednesday
    nxt = _next(ev, now)
    assert nxt is not None
    assert nxt.strftime("%a") == "Tue" and nxt.hour == 9
    assert (nxt - now).days < 7                            # the NEXT one, not the first ever


def test_a_finished_recurrence_is_not_resurrected():
    """COUNT=3 from January is long over; it must not be offered as 'next'."""
    ev = _parse(_ics(_vevent(
        "DTSTART;TZID=America/New_York:20260106T090000",
        "RRULE:FREQ=DAILY;COUNT=3",
        "SUMMARY:Three days only",
    )))[0]
    assert _next(ev, TZ.localize(datetime(2026, 7, 15, 12, 0))) is None


def test_an_excluded_occurrence_is_skipped():
    ev = _parse(_ics(_vevent(
        "DTSTART;TZID=America/New_York:20260701T090000",
        "RRULE:FREQ=DAILY",
        "EXDATE;TZID=America/New_York:20260716T090000",
        "SUMMARY:Daily",
    )))[0]
    now = TZ.localize(datetime(2026, 7, 15, 12, 0))
    nxt = _next(ev, now)
    assert nxt.day == 17, "the 16th was excluded, so the next one is the 17th"


def test_an_event_beyond_the_horizon_is_not_next():
    ev = _parse(_ics(_vevent("DTSTART:20270716T130000Z", "SUMMARY:Next year")))[0]
    now = TZ.localize(datetime(2026, 7, 15, 12, 0))
    assert _next(ev, now, days=60) is None


# --- the page ---------------------------------------------------------------

def _render(rows, cols, ics, **extra):
    """Through the real runtime, with the feed served from a stub instead of the network."""
    settings = {"timezone": "US/Eastern", "language": "en-US",
                "plugin_calendar_ical_url": "https://example.invalid/cal.ics"}
    settings.update({f"plugin_calendar_{k}": v for k, v in extra.items()})
    rt = make_runtime(installed=["calendar"], rows=rows, cols=cols, settings=settings)
    return rt.get_pages("calendar")


@pytest.fixture
def feed(monkeypatch):
    """Serve feeds without touching the network.

    Keyed by URL, because the app takes SEVERAL calendars and the interesting case is one of
    them being down: that must cost you the events in that feed, not the whole app.
    """
    import requests

    holder = {"text": _ics(), "by_url": {}, "dead": set()}

    class _Resp:
        def __init__(self, text):
            self.text = text
            self.status_code = 200

        def raise_for_status(self):
            pass

    def _get(url, *a, **k):
        if url in holder["dead"]:
            raise requests.exceptions.ConnectionError("that calendar is down")
        return _Resp(holder["by_url"].get(url, holder["text"]))

    monkeypatch.setattr(requests, "get", _get)
    return holder


def _lines(page, rows, cols):
    return [page[r * cols:(r + 1) * cols].strip() for r in range(rows)]


def test_a_tall_wall_shows_the_second_event_too(feed):
    """'The next event, or two if the screen permits it' — four rows is what two events cost."""
    soon = datetime.now(TZ) + timedelta(days=2)
    later = datetime.now(TZ) + timedelta(days=3)
    feed["text"] = _ics(
        _vevent(f"DTSTART;TZID=America/New_York:{soon:%Y%m%d}T090000", "SUMMARY:Dentist"),
        _vevent(f"DTSTART;TZID=America/New_York:{later:%Y%m%d}T140000", "SUMMARY:Haircut"),
    )
    tall = " ".join(_lines(_render(5, 15, feed["text"])[0], 5, 15))
    assert "Dentist" in tall and "Haircut" in tall

    short = " ".join(_lines(_render(3, 15, feed["text"])[0], 3, 15))
    assert "Dentist" in short and "Haircut" not in short, "3 rows has room for one event"


def test_the_soonest_event_is_the_one_shown(feed):
    """The feed is not in date order; the app must sort, not take the first VEVENT."""
    far = datetime.now(TZ) + timedelta(days=20)
    near = datetime.now(TZ) + timedelta(days=2)
    feed["text"] = _ics(
        _vevent(f"DTSTART;TZID=America/New_York:{far:%Y%m%d}T090000", "SUMMARY:Later thing"),
        _vevent(f"DTSTART;TZID=America/New_York:{near:%Y%m%d}T090000", "SUMMARY:Sooner thing"),
    )
    page = " ".join(_lines(_render(3, 15, feed["text"])[0], 3, 15))
    assert "Sooner thing" in page and "Later thing" not in page


def test_todays_all_day_event_has_not_expired(feed):
    """A birthday is on all day. It must not vanish at 00:01 and report next year's."""
    today = datetime.now(TZ)
    feed["text"] = _ics(
        _vevent(f"DTSTART;VALUE=DATE:{today:%Y%m%d}", "SUMMARY:Birthday"))
    lines = _lines(_render(3, 15, feed["text"])[0], 3, 15)
    assert "Birthday" in " ".join(lines)
    assert "Today" in " ".join(lines)


def test_an_all_day_event_shows_no_clock(feed):
    """It has no time. Printing midnight would be inventing one."""
    soon = datetime.now(TZ) + timedelta(days=3)
    feed["text"] = _ics(_vevent(f"DTSTART;VALUE=DATE:{soon:%Y%m%d}", "SUMMARY:Holiday"))
    page = " ".join(_lines(_render(3, 15, feed["text"])[0], 3, 15))
    assert "AM" not in page and "PM" not in page and "00:00" not in page


def test_all_day_events_can_be_skipped(feed):
    soon = datetime.now(TZ) + timedelta(days=2)
    feed["text"] = _ics(
        _vevent(f"DTSTART;VALUE=DATE:{soon:%Y%m%d}", "SUMMARY:Birthday"),
        _vevent(f"DTSTART;TZID=America/New_York:{soon:%Y%m%d}T090000", "SUMMARY:Dentist"),
    )
    page = " ".join(_lines(_render(3, 15, feed["text"], skip_all_day="yes")[0], 3, 15))
    assert "Dentist" in page and "Birthday" not in page


def test_it_asks_to_be_configured_before_it_has_a_url(feed):
    page = " ".join(_lines(_render(3, 15, feed["text"], ical_url="")[0], 3, 15))
    assert "Configure" in page and "iCal" in page


def test_an_unreachable_feed_says_so_rather_than_crashing(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("no network")

    monkeypatch.setattr(requests, "get", boom)
    page = " ".join(_lines(_render(3, 15, _ics())[0], 3, 15))
    assert "Offline" in page


def test_an_empty_calendar_says_so(feed):
    feed["text"] = _ics()
    page = " ".join(_lines(_render(3, 15, feed["text"])[0], 3, 15))
    assert "No events" in page


# --- several calendars ------------------------------------------------------

WORK = "https://example.invalid/work.ics"
HOME = "https://example.invalid/home.ics"


def test_several_calendars_merge_into_one_timeline(feed):
    """Work, family, birthdays: separate feeds, one wall. The soonest thing wins regardless
    of which calendar it came from."""
    near = datetime.now(TZ) + timedelta(days=1)
    far = datetime.now(TZ) + timedelta(days=4)
    feed["by_url"] = {
        WORK: _ics(_vevent(f"DTSTART;TZID=America/New_York:{far:%Y%m%d}T090000", "SUMMARY:Review")),
        HOME: _ics(_vevent(f"DTSTART;TZID=America/New_York:{near:%Y%m%d}T180000", "SUMMARY:Dinner")),
    }
    page = " ".join(_lines(_render(5, 15, "", ical_url=f"{WORK},{HOME}")[0], 5, 15))
    assert "Dinner" in page and "Review" in page
    assert page.index("Dinner") < page.index("Review"), "sorted by time, not by feed"


def test_one_dead_calendar_does_not_hide_the_others(feed):
    """A feed being down costs you ITS events — not the whole app. Going Offline here would
    blank a wall that still has something true to say."""
    soon = datetime.now(TZ) + timedelta(days=1)
    feed["by_url"] = {
        HOME: _ics(_vevent(f"DTSTART;TZID=America/New_York:{soon:%Y%m%d}T180000", "SUMMARY:Dinner")),
    }
    feed["dead"] = {WORK}
    page = " ".join(_lines(_render(3, 15, "", ical_url=f"{WORK},{HOME}")[0], 3, 15))
    assert "Dinner" in page
    assert "Offline" not in page


def test_offline_only_when_every_calendar_is_down(feed):
    feed["dead"] = {WORK, HOME}
    page = " ".join(_lines(_render(3, 15, "", ical_url=f"{WORK},{HOME}")[0], 3, 15))
    assert "Offline" in page


def test_the_urls_can_be_separated_by_commas_or_newlines():
    """People paste long URLs, and pasting brings newlines with it."""
    assert cal._urls(f"{WORK},{HOME}") == [WORK, HOME]
    assert cal._urls(f"{WORK}\n{HOME}") == [WORK, HOME]
    assert cal._urls(f"  {WORK} ,\n\n {HOME}  ,") == [WORK, HOME]
    assert cal._urls("") == [] and cal._urls(None) == []
