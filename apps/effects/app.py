"""On-device panel effects for a Matrix wall (the `canvas`/`effects` capability).

The panel renders these itself, at its native ~70 fps, with nothing on the
network — the companion just names the effect once. A canvas app: it draws to
the framebuffer through the injected `canvas` helper instead of returning flap
pages, and the engine only runs it on a wall that has a framebuffer.
"""


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None                       # no framebuffer on this wall — nothing to do
    effect = str(settings.get('effect', 'plasma') or 'plasma').lower()
    if canvas.effects and effect not in canvas.effects:
        effect = canvas.effects[0]        # this panel doesn't have that one — use its first
    try:
        speed = int(float(settings.get('speed', 5) or 5))
    except (TypeError, ValueError):
        speed = 5
    # hue (0-255) and density (1-100) tint/seed the effects that support them — but only
    # where the wall advertises the knobs (caps.effect_params) and only when actually set;
    # blank means "keep the effect's own default look".
    params = getattr(canvas, 'effect_params', ())

    def _opt(key, lo, hi):
        raw = str(settings.get(key, '') or '').strip()
        if not raw or key not in params:
            return None
        try:
            return max(lo, min(hi, int(float(raw))))
        except (TypeError, ValueError):
            return None

    canvas.effect(effect, speed, hue=_opt('hue', 0, 255), density=_opt('density', 1, 100))
    # The panel renders on its own now; re-affirm only occasionally (loop_delay).
    return None
