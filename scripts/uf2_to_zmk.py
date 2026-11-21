#!/usr/bin/env python3
"""Convert a MoErgo Go60 ZMK firmware (.uf2) into the Sofle keymap in this repo.

The UF2 is compiled firmware, so there is no keymap source in it.  ZMK stores the
keymap as a flat C array of `struct zmk_behavior_binding { char *dev; u32 p1, p2; }`
and combos as small structs next to it.  This script locates and decodes those,
maps the 60 Go60 positions onto the Sofle's 64 positions, and rewrites the
`layer_*` bindings and combos of `config/eyelash_sofle.keymap` in place.
Macros, behaviors, encoder (sensor) bindings and hold-tap timings are NOT in the
firmware and are left untouched.

Usage:
    python3 scripts/uf2_to_zmk.py ~/Downloads/mappings.uf2 --dry-run
    python3 scripts/uf2_to_zmk.py ~/Downloads/mappings.uf2

Only the Python standard library is needed.
"""
import argparse
import re
import struct
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Position mapping: Go60 index -> Sofle index (None = no Sofle equivalent)
#
# Go60 order (ZMK matrix transform): rows 0-3 are 12 keys each (6 left + 6
# right) = 0..47, then the 6 bottom keys 48..53 (3 left, 3 right), then the
# thumb keys 54..59 (3 left, 3 right, listed left-to-right).
# Sofle: 13 positions per row (6 left, inner column, 6 right); last row has
# left 52..57, inner 58, right 59..63.  Inner column positions never exist
# physically.  The two innermost Go60 thumbs (56 and 57) have no Sofle key.
# EDIT THIS TABLE if your physical mapping differs.
# --------------------------------------------------------------------------- #
SOFLE_KEYS = 64
SOFLE_ROWS = [13, 13, 13, 13, 12]
SOFLE_INNER = [6, 19, 32, 45, 58]


def build_position_map():
    m = {}
    for g in range(48):
        r, c = divmod(g, 12)
        m[g] = r * 13 + (c if c < 6 else c + 1)
    m.update({48: 53, 49: 54, 50: 55, 51: 61, 52: 62, 53: 63})
    m.update({54: 56, 55: 57, 56: None, 57: None, 58: 59, 59: 60})
    return m


POS_MAP = build_position_map()
GO60_KEYS = 60

# --------------------------------------------------------------------------- #
# UF2 + firmware parsing
# --------------------------------------------------------------------------- #

def read_uf2(path):
    data = Path(path).read_bytes()
    chunks = {}
    for i in range(0, len(data) - 511, 512):
        b = data[i:i + 512]
        m0, m1 = struct.unpack_from('<II', b, 0)
        if m0 != 0x0A324655 or m1 != 0x9E5D5157:
            continue
        addr, size = struct.unpack_from('<II', b, 12)
        chunks[addr] = b[32:32 + size]
    if not chunks:
        sys.exit('error: not a valid UF2 file')
    base = min(chunks)
    end = max(a + len(c) for a, c in chunks.items())
    img = bytearray(end - base)
    for a, c in chunks.items():
        img[a - base:a - base + len(c)] = c
    return base, bytes(img)


class Fw:
    def __init__(self, base, img):
        self.base, self.img = base, img

    def cstr(self, ptr):
        o = ptr - self.base
        if not 0 <= o < len(self.img):
            return None
        e = self.img.find(b'\0', o)
        t = self.img[o:e]
        if 1 <= len(t) <= 40 and re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_]*', t):
            return t.decode()
        return None

    def find_keymap(self):
        best, off, n = [], 0, len(self.img)
        while off <= n - 12:
            run, o = [], off
            while o <= n - 12:
                p, a, b = struct.unpack_from('<III', self.img, o)
                name = self.cstr(p)
                if not name:
                    break
                run.append((name, a, b))
                o += 12
            if len(run) > len(best):
                best, start = run, off
            off = o + 4 if run else off + 4
        # trim ZMK event-name junk at the ends
        while best and best[0][0] not in BEHAVIORS:
            best.pop(0)
        while best and best[-1][0] not in BEHAVIORS:
            best.pop()
        return best

    def find_combos(self):
        """Pattern: positions[N], (layer<<16|N), timeout, prior_idle, binding."""
        out = []
        words = struct.unpack_from('<%dI' % (len(self.img) // 4), self.img, 0)
        for i in range(4, len(words) - 6):
            w = words[i]
            n = w & 0xFFFF
            if not (2 <= n <= 4):
                continue
            timeout, prior = words[i + 1], words[i + 2]
            name = self.cstr(words[i + 3])
            if not name or not (1 <= timeout <= 2000) or prior > 2000:
                continue
            pos = list(words[i - n:i])
            if all(0 <= p < GO60_KEYS for p in pos) and (w >> 16) == 0xFFFF:
                out.append({'positions': pos, 'timeout': timeout,
                            'binding': (name, words[i + 4], words[i + 5])})
        return out


# --------------------------------------------------------------------------- #
# Decoding bindings to ZMK source
# --------------------------------------------------------------------------- #
KEYS = {}
for _i, _c in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
    KEYS[4 + _i] = _c
for _i in range(9):
    KEYS[30 + _i] = 'N%d' % (_i + 1)
KEYS[39] = 'N0'
KEYS.update({40: 'RET', 41: 'ESC', 42: 'BSPC', 43: 'TAB', 44: 'SPACE', 45: 'MINUS',
             46: 'EQUAL', 47: 'LBKT', 48: 'RBKT', 49: 'BSLH', 50: 'NUHS', 51: 'SEMI',
             52: 'SQT', 53: 'GRAVE', 54: 'COMMA', 55: 'DOT', 56: 'FSLH', 57: 'CAPS',
             70: 'PSCRN', 71: 'SLCK', 72: 'PAUSE_BREAK', 73: 'INS', 74: 'HOME',
             75: 'PG_UP', 76: 'DEL', 77: 'END', 78: 'PG_DN', 79: 'RIGHT', 80: 'LEFT',
             81: 'DOWN', 82: 'UP', 83: 'KP_NUM', 84: 'KP_DIVIDE', 85: 'KP_MULTIPLY',
             86: 'KP_MINUS', 87: 'KP_PLUS', 88: 'KP_ENTER', 98: 'KP_N0', 99: 'KP_DOT',
             224: 'LCTRL', 225: 'LSHFT', 226: 'LALT', 227: 'LGUI',
             228: 'RCTRL', 229: 'RSHFT', 230: 'RALT', 231: 'RGUI'})
for _i in range(12):
    KEYS[58 + _i] = 'F%d' % (_i + 1)
    KEYS[104 + _i] = 'F%d' % (_i + 13)
for _i in range(9):
    KEYS[89 + _i] = 'KP_N%d' % (_i + 1)
CONSUMER = {0xE2: 'C_MUTE', 0xE9: 'C_VOL_UP', 0xEA: 'C_VOL_DN', 0xB5: 'C_NEXT',
            0xB6: 'C_PREV', 0xB7: 'C_STOP', 0xB8: 'C_EJECT', 0xCD: 'C_PP',
            0x6F: 'C_BRI_UP', 0x70: 'C_BRI_DN'}
MODS = [(0x01, 'LC'), (0x02, 'LS'), (0x04, 'LA'), (0x08, 'LG'),
        (0x10, 'RC'), (0x20, 'RS'), (0x40, 'RA'), (0x80, 'RG')]
RGB = ['RGB_TOG', 'RGB_ON', 'RGB_OFF', 'RGB_HUI', 'RGB_HUD', 'RGB_SAI', 'RGB_SAD',
       'RGB_BRI', 'RGB_BRD', 'RGB_SPI', 'RGB_SPD', 'RGB_EFF', 'RGB_EFR']
BT = ['BT_CLR', 'BT_NXT', 'BT_PRV', 'BT_SEL', 'BT_CLR_ALL', 'BT_DISC']
OUT = ['OUT_TOG', 'OUT_USB', 'OUT_BLE']
MB = {1: 'MB1', 2: 'MB2', 4: 'MB3', 8: 'MB4', 16: 'MB5'}

# firmware device name -> (zmk label, param kinds)
BEHAVIORS = {
    'key_press': ('kp', 'k'), 'none': ('none', ''), 'transparent': ('trans', ''),
    'layer_tap': ('lt', 'lk'), 'layer_tap_optimized': ('lt_optimized', 'lk'),
    'mod_tap': ('mt', 'kk'), 'HRM_Hold_Tap_lha': ('HRM_Hold_Tap_lha', 'kk'),
    'HRM_Hold_Tap_rh': ('HRM_Hold_Tap_rh', 'kk'),
    'cmd_sticky_shift': ('cmd_sticky_shift', 'kk'),
    'momentary_layer': ('mo', 'l'), 'to_layer': ('to', 'l'), 'toggle_layer': ('tog', 'l'),
    'sticky_layer': ('sl', 'l'), 'bootload': ('bootloader', ''),
    'sysreset': ('sys_reset', ''), 'bluetooth': ('bt', 'bt'), 'outputs': ('out', 'out'),
    'rgb_ug': ('rgb_ug', 'rgb'), 'mouse_key_press': ('mkp', 'mb'),
    'mouse_move': ('mmv', 'mv'), 'mouse_scroll': ('msc', 'sc'),
    'BT_0': ('bt_0', ''), 'BT_1': ('bt_1', ''), 'BT_2': ('bt_2', ''), 'BT_3': ('bt_3', ''),
}


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


class Decoder:
    def __init__(self, layer_names):
        self.layer_names = layer_names  # idx -> name
        self.warnings = []

    def key(self, u):
        mods, page, kid = (u >> 24) & 0xFF, (u >> 16) & 0xFF, u & 0xFFFF
        if page in (0, 7):
            name = KEYS.get(kid)
        elif page == 0x0C:
            name = CONSUMER.get(kid)
        else:
            name = None
        if name is None:
            self.warnings.append('unknown key usage 0x%x' % u)
            name = '0x%x' % (u & 0xFFFFFF)
        for bit, m in reversed(MODS):
            if mods & bit:
                name = '%s(%s)' % (m, name)
        return name

    def layer(self, i):
        return 'LAYER_%s' % self.layer_names[i] if i in self.layer_names else str(i)

    def param(self, kind, v):
        if kind == 'k':
            return self.key(v)
        if kind == 'l':
            return self.layer(v)
        if kind == 'out':
            return OUT[v] if v < len(OUT) else str(v)
        if kind == 'rgb':
            return RGB[v] if v < len(RGB) else str(v)
        if kind == 'mb':
            return MB.get(v, '0x%x' % v)
        if kind == 'sc':
            x, y = s16(v >> 16), s16(v & 0xFFFF)
            names = {(0, 10): 'SCRL_UP', (0, -10): 'SCRL_DOWN', (-10, 0): 'SCRL_LEFT', (10, 0): 'SCRL_RIGHT'}
            if (x, y) in names:
                return names[(x, y)]
        if kind in ('mv', 'sc'):
            x, y = s16(v >> 16), s16(v & 0xFFFF)
            return 'MOVE_X(%d)' % x if y == 0 else 'MOVE_Y(%d)' % y if x == 0 else \
                '(MOVE_X(%d)|MOVE_Y(%d))' % (x, y)
        return str(v)

    def binding(self, b):
        name, p1, p2 = b
        if name not in BEHAVIORS:
            self.warnings.append('unknown behavior %r' % name)
            return '&none /* TODO: %s 0x%x 0x%x */' % (name, p1, p2)
        label, kinds = BEHAVIORS[name]
        if kinds == 'bt':
            return '&bt %s' % BT[p1] + (' %d' % p2 if p1 == 3 else '') if p1 < len(BT) \
                else '&bt %d %d' % (p1, p2)
        ks = [kinds] if kinds in ('rgb', 'out', 'mb', 'mv', 'sc') else list(kinds)
        args = [self.param(k, v) for k, v in zip(ks, (p1, p2))]
        return ' '.join(['&' + label] + args)


# --------------------------------------------------------------------------- #
# Keymap file handling
# --------------------------------------------------------------------------- #

def strip_comments(t):
    return re.sub(r'/\*.*?\*/', '', t, flags=re.S)


def split_tokens(text):
    return re.findall(r'&\w+(?:\s+(?!&)[^\s&]+)*', strip_comments(text))


NODE_FOR = {}  # layer name -> node name


def find_layers(src):
    """Return [(index, name, node)] using the #define LAYER_x N lines."""
    defs = {int(n): name for name, n in re.findall(r'#define LAYER_(\w+)\s+(\d+)', src)}
    return defs


def old_layer_tokens(src, name):
    m = re.search(r'layer_%s \{\s*bindings = <(.*?)>;' % name, src, re.S)
    return split_tokens(m.group(1)) if m else None


SHORT = {'LCTRL': 'Ctrl', 'RCTRL': 'Ctrl', 'LSHFT': 'Sft', 'RSHFT': 'Sft', 'LSHIFT': 'Sft',
         'RSHIFT': 'Sft', 'LALT': 'Alt', 'RALT': 'Alt', 'LGUI': 'Gui', 'RGUI': 'Gui',
         'SPACE': 'Spc', 'BSPC': 'BSPC', 'MINUS': '-', 'EQUAL': '=', 'COMMA': ',',
         'DOT': '.', 'FSLH': '/', 'BSLH': '\\', 'SEMI': ';', 'SQT': "'", 'GRAVE': '`',
         'LBKT': '[', 'RBKT': ']', 'LEFT': '←', 'RIGHT': '→', 'UP': '↑', 'DOWN': '↓',
         'N1': '1', 'N2': '2', 'N3': '3', 'N4': '4', 'N5': '5', 'N6': '6', 'N7': '7',
         'N8': '8', 'N9': '9', 'N0': '0', 'RET': 'Ent'}


def short(a):
    a = a.replace('LAYER_', '')
    m = re.fullmatch(r'(L|R)([CSAG])\((.*)\)', a)
    if m:
        return {'C': '⌃', 'S': '⇧', 'A': '⌥', 'G': '⌘'}[m.group(2)] + short(m.group(3))
    return SHORT.get(a, a)


def label(tok):
    p = tok.split()
    b, args = p[0], p[1:]
    if b == '&none':
        return ''
    if b == '&trans':
        return '▽'
    if b in ('&lt', '&lt_optimized'):
        return short(args[-1])[:6]
    s = '/'.join(short(a) for a in args) if args else b[1:]
    return s[:6]


def cell(s):
    s = s[:6]
    pad = 6 - len(s)
    return ' ' * (pad // 2) + s + ' ' * (pad - pad // 2)


def ascii_art(tokens):
    def line(r):
        cells = []
        idx = list(range(r * 13, r * 13 + SOFLE_ROWS[r]))
        L, R = idx[:6], idx[7:]
        return L, R
    out = []
    for r in range(5):
        L, R = line(r)
        if r == 0:
            out.append('┌' + '┬'.join(['──────'] * 6) + '┐ ┌──────┐ ┌' + '┬'.join(['──────'] * 6) + '┐')
        else:
            right_sep = '├' + '┼'.join(['──────'] * len(R)) + ('┤' if len(R) == 6 else '┤')
            out.append('├' + '┼'.join(['──────'] * 6) + '┤ │      │ ' + right_sep)
        out.append('│' + '│'.join(cell(label(tokens[i])) for i in L) + '│ │      │ │' +
                   '│'.join(cell(label(tokens[i])) for i in R) + '│' + ('' if len(R) == 6 else ' ' * 0))
    out.append('└' + '┴'.join(['──────'] * 6) + '┘ └──────┘ └' + '┴'.join(['──────'] * 5) + '┘')
    return out


def format_bindings(tokens):
    rows, i = [], 0
    for n in SOFLE_ROWS:
        rows.append(tokens[i:i + n])
        i += n
    ncol = max(SOFLE_ROWS)
    widths = [max((len(r[c]) for r in rows if c < len(r)), default=0) + 2 for c in range(ncol)]
    lines = []
    for r in rows:
        lines.append(''.join(t.ljust(widths[c]) for c, t in enumerate(r)).rstrip())
    return '\n'.join(lines)


def replace_layer(src, name, title_lines, tokens, display=None):
    art = ascii_art(tokens)
    comment_body = '\n'.join('         * ' + l for l in title_lines + art)
    comment = '/*\n' + comment_body + '\n         */'
    pat = re.compile(r'(?:/\*(?:(?!\*/).)*?\*/\s*\n)?(\s*)layer_%s \{\s*bindings = <\n?.*?>;' % name, re.S)
    m = pat.search(src)
    new = ('%s\n        layer_%s {\n            bindings = <\n%s\n            >;'
           % (comment, name, format_bindings(tokens)))
    if m:
        return src[:m.start()] + new + src[m.end():]
    return None


def title_of(src, name):
    m = re.search(r'/\*((?:(?!\*/).)*?)\*/\s*\n\s*layer_%s \{' % name, src, re.S)
    if not m:
        return [name.upper() + ' LAYER']
    out = []
    for l in m.group(1).split('\n'):
        l = re.sub(r'^\s*\*? ?', '', l)
        if '┌' in l:
            break
        if l.strip():
            out.append(l)
    return out or [name.upper() + ' LAYER']


def parse_old_combos(src):
    m = re.search(r'    combos \{\n(.*?)\n    \};\n\};', src, re.S)
    body = m.group(1)
    blocks = []
    for bm in re.finditer(r'((?:[ \t]*/\*[^\n]*\*/[ \t]*\n)?[ \t]*(\w+) \{\n(.*?)\n[ \t]*\};)', body, re.S):
        inner = bm.group(3)
        pos = re.search(r'key-positions = <([^>]*)>', inner)
        bnd = re.search(r'bindings = <([^>]*)>', inner)
        if pos and bnd:
            blocks.append({'name': bm.group(2), 'text': bm.group(1),
                           'positions': tuple(sorted(int(x) for x in pos.group(1).split())),
                           'binding': ' '.join(bnd.group(1).split())})
    return m, blocks


def layer_refs(tok, names):
    return {i for i, n in names.items() if re.search(r'LAYER_%s\b' % n, tok)}


def reachable(layers, names):
    seen, todo = {0}, [0]
    while todo:
        cur = todo.pop()
        for t in layers.get(cur, []):
            for r in layer_refs(t, names):
                if r not in seen:
                    seen.add(r)
                    todo.append(r)
    return seen


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('uf2')
    ap.add_argument('--keymap', default=str(Path(__file__).resolve().parent.parent / 'config/eyelash_sofle.keymap'))
    ap.add_argument('--dry-run', action='store_true', help='print report, do not write')
    ap.add_argument('--keep-combo', action='append', default=[], metavar='NAME',
                    help='keep an existing Sofle combo that is not in the firmware (repeatable). '
                         'By default combos are an exact mirror of the firmware.')
    ap.add_argument('--layer-names', default='Base,Toggles,Raise,Macros,Magic,Mouse',
                    help='comma separated names for firmware layers 0..N (new names create new layers)')
    args = ap.parse_args()

    base, img = read_uf2(args.uf2)
    fw = Fw(base, img)
    raw = fw.find_keymap()
    nlayers = len(raw) // GO60_KEYS
    if nlayers == 0 or len(raw) % GO60_KEYS:
        print('warning: keymap length %d is not a multiple of %d' % (len(raw), GO60_KEYS))
    raw = raw[:nlayers * GO60_KEYS]

    keymap_path = Path(args.keymap)
    src = keymap_path.read_text()
    names = find_layers(src)
    wanted = args.layer_names.split(',')
    for i in range(nlayers):
        if i not in names:
            names[i] = wanted[i] if i < len(wanted) else 'Layer%d' % i
    dec = Decoder(names)

    print('Firmware: %d layers x %d keys decoded' % (nlayers, GO60_KEYS))
    new_layers, dropped = {}, []
    for L in range(nlayers):
        old = old_layer_tokens(src, names[L]) or ['&none'] * SOFLE_KEYS
        toks = list(old)
        for g in range(GO60_KEYS):
            t = dec.binding(raw[L * GO60_KEYS + g])
            s = POS_MAP[g]
            if s is None:
                if t not in ('&none', '&trans'):
                    dropped.append((names[L], g, t))
            else:
                toks[s] = t
        new_layers[L] = toks

    # keep access to layers that became unreachable (e.g. thumb layer keys were dropped)
    unreach = set(range(nlayers)) - reachable(new_layers, names)
    notes = []
    for L in sorted(unreach):
        old0 = old_layer_tokens(src, names[0]) or []
        hit = [(i, t) for i, t in enumerate(old0) if L in layer_refs(t, names)]
        for i, t in hit:
            new_layers[0][i] = t
            notes.append('layer %s was unreachable after dropping keys; kept old base binding %s at Sofle pos %d' % (names[L], t, i))
        if not hit:
            notes.append('WARNING: layer %s is unreachable and no old binding was found' % names[L])

    # combos
    fwc = fw.find_combos()
    _, old_combos = parse_old_combos(src)
    combo_blocks, combo_report = [], []
    for c in fwc:
        sp = [POS_MAP.get(p) for p in c['positions']]
        bnd = dec.binding(c['binding'])
        if None in sp:
            combo_report.append('skipped (uses dropped key): go60 %s -> %s' % (c['positions'], bnd))
            continue
        key = tuple(sorted(sp))
        match = next((o for o in old_combos if o['positions'] == key and o['binding'] == bnd), None)
        if match:
            combo_blocks.append(match['text'])
            combo_report.append('kept   %s %s -> %s' % (match['name'], list(key), bnd))
        else:
            lab = '_'.join(re.sub(r'\W', '', label(new_layers[0][p]) or str(p)) for p in sorted(sp))
            nm = 'combo_%s' % lab
            to = '' if c['timeout'] == 50 else '            timeout-ms = <%d>;\n' % c['timeout']
            combo_blocks.append('        /* From Go60 firmware: %s */\n        %s {\n%s            key-positions = <%s>;\n            bindings = <%s>;\n        };'
                                % (lab.replace('_', ' + '), nm, to, ' '.join(map(str, sorted(sp))), bnd))
            combo_report.append('added  %s %s -> %s' % (nm, list(key), bnd))
    for o in old_combos:
        if o['name'] in args.keep_combo:
            if o['text'] in combo_blocks:
                continue
            combo_blocks.insert(0, o['text'])
            combo_report.append('kept   %s (not in Go60 firmware) %s -> %s' % (o['name'], list(o['positions']), o['binding']))
        elif o['text'] not in combo_blocks:
            combo_report.append('REMOVED %s %s -> %s (not in firmware; use --keep-combo %s to keep)' % (o['name'], list(o['positions']), o['binding'], o['name']))

    print('\nCombos:')
    for l in combo_report:
        print('  ' + l)
    print('\nDropped Go60 keys (no Sofle position):')
    for l, g, t in dropped:
        print('  layer %-8s go60 pos %2d  %s' % (l, g, t))
    print('\nNotes:')
    for n in notes or ['none']:
        print('  ' + n)
    if dec.warnings:
        print('\nDecoder warnings:')
        for w in sorted(set(dec.warnings)):
            print('  ' + w)

    for L, toks in new_layers.items():
        assert len(toks) == SOFLE_KEYS, (L, len(toks))
        for i in SOFLE_INNER:
            assert toks[i] == '&none', (L, i, toks[i])

    if args.dry_run:
        print('\n--dry-run: nothing written')
        for L in range(nlayers):
            print('\n== %s ==' % names[L])
            print(format_bindings(new_layers[L]))
        return

    out = src
    for L in range(nlayers):
        nm = names[L]
        if re.search(r'layer_%s \{' % nm, out):
            r = replace_layer(out, nm, title_of(out, nm), new_layers[L])
            out = r
        else:  # brand-new layer: add define + node after the last layer
            out = re.sub(r'(#define LAYER_\w+ \d+\n)(?!#define LAYER_)', r'\1#define LAYER_%s %d\n' % (nm, L), out, count=1)
            node = ('\n        /*\n         * %s LAYER (generated from Go60 firmware)\n         */\n'
                    '        layer_%s {\n            bindings = <\n%s\n            >;\n'
                    '            sensor-bindings = <&rsr_vol &rsr_trans>;\n            display-name = "%s";\n        };\n'
                    % (nm.upper(), nm, format_bindings(new_layers[L]), nm))
            out = re.sub(r'(\n    \};\n\};\s*)$', lambda m: node + m.group(1), out)
            out = replace_layer(out, nm, [nm.upper() + ' LAYER (generated from Go60 firmware)'], new_layers[L])
    m, _ = parse_old_combos(out)
    new_combos = '    combos {\n        compatible = "zmk,combos";\n\n' + '\n\n'.join(combo_blocks) + '\n    };\n};'
    out = out[:m.start()] + new_combos + out[m.end():]
    keymap_path.write_text(out)

    # verification: re-read and compare
    again = keymap_path.read_text()
    bad = 0
    for L in range(nlayers):
        t = old_layer_tokens(again, names[L])
        if t != new_layers[L]:
            bad += 1
            print('VERIFY FAILED for layer', names[L], len(t or []))
    print('\nwrote %s (%d layers; verify %s)' % (keymap_path, nlayers, 'OK' if not bad else 'FAILED'))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
