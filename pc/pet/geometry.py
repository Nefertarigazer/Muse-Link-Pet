"""Logical window geometry shared with the frontend through layout.json.

Only the measured visible character is clamped. Transparent spill may leave
any screen edge; avail is the work-area/window intersection in logical px.
"""
import json
import math
from pathlib import Path

LAYOUT = json.loads((Path(__file__).parent / 'ui/layout.json').read_text(encoding='utf-8'))
UI_SCALE = LAYOUT['ui_scale']
SPILL = dict(left=0, right=0, top=0, bottom=0)
SPILL.update(LAYOUT.get('spill', {}))
CORE_WIDTH = math.ceil(LAYOUT['window']['width'] * UI_SCALE)
CORE_HEIGHT = math.ceil(LAYOUT['window']['height'] * UI_SCALE)
WINDOW_WIDTH = math.ceil((LAYOUT['window']['width'] + SPILL['left'] + SPILL['right']) * UI_SCALE)
WINDOW_HEIGHT = math.ceil((LAYOUT['window']['height'] + SPILL['top'] + SPILL['bottom']) * UI_SCALE)
CHAR_RECT = {k: v * UI_SCALE for k, v in LAYOUT.get('char_rect',
    dict(x=SPILL['left'], y=SPILL['top'], w=LAYOUT['window']['width'], h=LAYOUT['window']['height'])).items()}


def clamp_character(x, y, scale, area):
    c = CHAR_RECT
    return (max(math.ceil(area[0]-c['x']*scale), min(round(x),math.floor(area[2]-(c['x']+c['w'])*scale))),
            max(math.ceil(area[1]-c['y']*scale), min(round(y),math.floor(area[3]-(c['y']+c['h'])*scale))))



def available(x, y, width, height, scale, area):
    left, top = max(0, (area[0] - x) / scale), max(0, (area[1] - y) / scale)
    right, bottom = min(width, (area[2] - x) / scale), min(height, (area[3] - y) / scale)
    return dict(x=left, y=top, w=max(0, right-left), h=max(0, bottom-top))
