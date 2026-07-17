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
    canvas.effect(effect, speed)
    # The panel renders on its own now; re-affirm only occasionally (loop_delay).
    return None
