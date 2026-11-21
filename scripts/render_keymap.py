#!/usr/bin/env python3
"""Render the keymap image: parse -> draw -> light/dark SVG + 2x PNGs.

Requires: `pip install keymap-drawer` and a Chrome/Chromium binary for PNG export
(preinstalled on GitHub's ubuntu runners).  Use --no-png to skip PNG export.

    python3 scripts/render_keymap.py
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME_CANDIDATES = [
    os.environ.get('CHROME', ''), 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and (shutil.which(c) or Path(c).exists()):
            return shutil.which(c) or c
    return None


def run(cmd):
    print('+', ' '.join(map(str, cmd)))
    subprocess.run(cmd, check=True, cwd=ROOT)


def png(chrome, svg, out):
    w, h = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg.read_text()).groups()
    run([chrome, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-sandbox',
         '--force-device-scale-factor=2', f'--window-size={w},{h}', f'--screenshot={out}', svg.resolve().as_uri()])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keymap', default='config/eyelash_sofle.keymap')
    ap.add_argument('--layout', default='config/eyelash_sofle.json')
    ap.add_argument('--config', default='keymap_drawer.config.yaml')
    ap.add_argument('--out', default='keymap-drawer')
    ap.add_argument('--name', default='eyelash_sofle')
    ap.add_argument('--no-png', action='store_true')
    a = ap.parse_args()

    out = ROOT / a.out
    out.mkdir(exist_ok=True)
    yml, svg = out / f'{a.name}.yaml', out / f'{a.name}.svg'
    run(['keymap', '-c', a.config, 'parse', '-z', a.keymap, '-o', yml])
    run(['keymap', '-c', a.config, 'draw', yml, '-j', a.layout, '-o', svg])

    text = svg.read_text()
    # background panel (works when the SVG is shown through <img>)
    text = re.sub(r'(<svg [^>]*class="keymap"[^>]*>)', r'\1\n<rect class="bg" width="100%" height="100%" rx="18"/>', text, count=1)
    svg.write_text(text)

    light = out / f'{a.name}-light.svg'
    dark = out / f'{a.name}-dark.svg'
    light.write_text(text.replace('class="keymap"', 'class="keymap light"', 1))
    dark.write_text(text.replace('class="keymap"', 'class="keymap dark"', 1))

    if a.no_png:
        return
    chrome = find_chrome()
    if not chrome:
        sys.exit('Chrome/Chromium not found; set $CHROME or use --no-png')
    png(chrome, light, out / f'{a.name}.png')
    png(chrome, dark, out / f'{a.name}-dark.png')
    light.unlink()
    dark.unlink()


if __name__ == '__main__':
    main()
