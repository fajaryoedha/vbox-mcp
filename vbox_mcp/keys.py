"""Translate text and key names into PC/AT Set-1 scancodes for IKeyboard.putScancodes.

A key is a tuple of make-code bytes: (0x1E,) for 'a', (0xE0, 0x48) for the up arrow.
The break (release) code is the same sequence with 0x80 OR-ed into the last byte.
Layout is US QWERTY; the guest keyboard layout should match for type_text to be exact.
"""

E0 = 0xE0

KEYS: dict[str, tuple[int, ...]] = {
    "esc": (0x01,), "escape": (0x01,),
    "1": (0x02,), "2": (0x03,), "3": (0x04,), "4": (0x05,), "5": (0x06,),
    "6": (0x07,), "7": (0x08,), "8": (0x09,), "9": (0x0A,), "0": (0x0B,),
    "-": (0x0C,), "minus": (0x0C,), "=": (0x0D,), "equal": (0x0D,),
    "backspace": (0x0E,), "tab": (0x0F,),
    "q": (0x10,), "w": (0x11,), "e": (0x12,), "r": (0x13,), "t": (0x14,),
    "y": (0x15,), "u": (0x16,), "i": (0x17,), "o": (0x18,), "p": (0x19,),
    "[": (0x1A,), "]": (0x1B,), "enter": (0x1C,), "return": (0x1C,),
    "ctrl": (0x1D,), "lctrl": (0x1D,), "control": (0x1D,),
    "a": (0x1E,), "s": (0x1F,), "d": (0x20,), "f": (0x21,), "g": (0x22,),
    "h": (0x23,), "j": (0x24,), "k": (0x25,), "l": (0x26,),
    ";": (0x27,), "'": (0x28,), "`": (0x29,),
    "shift": (0x2A,), "lshift": (0x2A,), "\\": (0x2B,),
    "z": (0x2C,), "x": (0x2D,), "c": (0x2E,), "v": (0x2F,), "b": (0x30,),
    "n": (0x31,), "m": (0x32,), ",": (0x33,), ".": (0x34,), "/": (0x35,),
    "rshift": (0x36,), "kp*": (0x37,), "alt": (0x38,), "lalt": (0x38,),
    "space": (0x39,), " ": (0x39,), "capslock": (0x3A,),
    "f1": (0x3B,), "f2": (0x3C,), "f3": (0x3D,), "f4": (0x3E,), "f5": (0x3F,),
    "f6": (0x40,), "f7": (0x41,), "f8": (0x42,), "f9": (0x43,), "f10": (0x44,),
    "numlock": (0x45,), "scrolllock": (0x46,),
    "kp7": (0x47,), "kp8": (0x48,), "kp9": (0x49,), "kp-": (0x4A,),
    "kp4": (0x4B,), "kp5": (0x4C,), "kp6": (0x4D,), "kp+": (0x4E,),
    "kp1": (0x4F,), "kp2": (0x50,), "kp3": (0x51,), "kp0": (0x52,), "kp.": (0x53,),
    "f11": (0x57,), "f12": (0x58,),
    # extended keys
    "kpenter": (E0, 0x1C), "rctrl": (E0, 0x1D), "kp/": (E0, 0x35),
    "ralt": (E0, 0x38), "altgr": (E0, 0x38),
    "home": (E0, 0x47), "up": (E0, 0x48), "pageup": (E0, 0x49), "pgup": (E0, 0x49),
    "left": (E0, 0x4B), "right": (E0, 0x4D), "end": (E0, 0x4F),
    "down": (E0, 0x50), "pagedown": (E0, 0x51), "pgdn": (E0, 0x51),
    "insert": (E0, 0x52), "ins": (E0, 0x52), "delete": (E0, 0x53), "del": (E0, 0x53),
    "win": (E0, 0x5B), "lwin": (E0, 0x5B), "super": (E0, 0x5B), "meta": (E0, 0x5B),
    "cmd": (E0, 0x5B), "rwin": (E0, 0x5C), "menu": (E0, 0x5D), "apps": (E0, 0x5D),
    "printscreen": (E0, 0x37), "prtsc": (E0, 0x37),
}

# Characters typed with Shift held, mapped to their unshifted key.
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

SPECIAL_CHARS = {"\n": "enter", "\t": "tab", "\b": "backspace"}


def make(key: tuple[int, ...]) -> list[int]:
    return list(key)


def brk(key: tuple[int, ...]) -> list[int]:
    return list(key[:-1]) + [key[-1] | 0x80]


def lookup(name: str) -> tuple[int, ...]:
    key = KEYS.get(name.lower())
    if key is None:
        raise ValueError(f"Unknown key {name!r}. Known keys: {', '.join(sorted(KEYS))}")
    return key


def combo(spec: str) -> list[int]:
    """'ctrl+shift+esc' -> press all keys in order, release in reverse order."""
    keys = [lookup(p.strip()) for p in spec.split("+") if p.strip()]
    if not keys:
        raise ValueError(f"Empty key combo {spec!r}")
    codes: list[int] = []
    for k in keys:
        codes += make(k)
    for k in reversed(keys):
        codes += brk(k)
    return codes


def char_codes(ch: str) -> list[int]:
    if ch in SPECIAL_CHARS:
        k = KEYS[SPECIAL_CHARS[ch]]
        return make(k) + brk(k)
    shift = False
    if ch in SHIFTED:
        base, shift = SHIFTED[ch], True
    elif ch.isascii() and ch.isalpha() and ch.isupper():
        base, shift = ch.lower(), True
    else:
        base = ch
    k = KEYS.get(base)
    if k is None or len(base) != 1:
        raise ValueError(f"Character {ch!r} cannot be typed with a US keyboard layout")
    sh = KEYS["shift"]
    codes = make(k) + brk(k)
    return make(sh) + codes + brk(sh) if shift else codes


def text_codes(text: str) -> list[list[int]]:
    """One scancode group per character (validated up front so nothing is half-typed)."""
    return [char_codes(ch) for ch in text]
