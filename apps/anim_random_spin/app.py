"""Continuous random spin — each module independently flips to random characters."""

import random
import time

CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;:%'

_module_state = []


def fetch(settings, format_lines, get_rows, get_cols):
    global _module_state

    n = get_rows() * get_cols()
    dwell = float(settings.get("anim_speed", 0.5))

    # Initialize or resize state
    if len(_module_state) != n:
        _module_state = []
        for _ in range(n):
            _module_state.append({
                "current": random.choice(CHARS),
                "target": random.choice(CHARS),
                "dwelling": False,
                "until": 0
            })

    now = time.time()
    output = []

    for mod in _module_state:
        if mod["dwelling"]:
            # Module is paused on a character — check if dwell time expired
            if now >= mod["until"]:
                mod["target"] = random.choice(CHARS)
                mod["dwelling"] = False
        else:
            # Module is spinning — pick a random char each tick
            mod["current"] = random.choice(CHARS)
            # ~8% chance per tick of "arriving" at target (staggers stops naturally)
            if random.random() < 0.08:
                mod["current"] = mod["target"]
            if mod["current"] == mod["target"]:
                mod["dwelling"] = True
                mod["until"] = now + dwell

        output.append(mod["current"])

    return [{"text": ''.join(output), "style": "random", "speed": 30}]
