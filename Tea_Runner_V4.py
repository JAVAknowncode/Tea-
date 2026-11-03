import argparse
import ast
import copy
import difflib
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any


# ==========================================
# Errors and internal control-flow signals
# ==========================================
class TeaError(Exception):
    """Base class for user-facing Tea errors."""
    kind = "Tea Error"

    def __init__(self, message: str, line_num: int | None = None, file_name: str | None = None):
        self.message = message
        self.line_num = line_num
        self.file_name = file_name
        location = f" {file_name}:{line_num}" if line_num is not None else ""
        super().__init__(f"\n[{self.kind}]{location}\n  -> {message}")


class TeaSyntaxError(TeaError):
    kind = "Tea Syntax Error"


class TeaRuntimeError(TeaError):
    kind = "Tea Runtime Error"


def format_error(exc: TeaError, source_lines: list[str] | None) -> str:
    """Render an error with the offending source line and a caret."""
    text = str(exc)
    if source_lines and exc.line_num and 1 <= exc.line_num <= len(source_lines):
        raw = source_lines[exc.line_num - 1].rstrip("\n").replace("\t", "    ")
        if raw.strip():
            shown = raw if len(raw) <= 100 else raw[:100] + "..."
            text += f"\n  | {shown.strip()}"
    return text


def suggest(name: str, candidates) -> str:
    """'did you mean' hint for a misspelled identifier."""
    hits = difflib.get_close_matches(name, sorted(candidates), n=1, cutoff=0.7)
    return f" Did you mean '{hits[0]}'?" if hits else ""


class _ReturnSignal(Exception):
    def __init__(self, value: Any):
        self.value = value


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


# ==========================================
# Limits
# ==========================================
MAX_EXPR_DEPTH = 100
MAX_REPEAT = 10_000
MAX_POW_EXP = 1_000
MAX_INT_BITS = 100_000
MAX_LOOP_DEPTH = 32
MAX_BLOCK_DEPTH = 64
MAX_STR_CHARS = 1_000_000
MAX_LIST_ITEMS = 100_000
MAX_MAP_ITEMS = 100_000
MAX_ERR_TEXT = 120
MAX_SLEEP_SECONDS = 5.0

BOOTSTRAP = "import.tea.util;"
IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")
RESERVED = {
    "true", "false", "null", "variable", "function", "if", "else", "loops",
    "foreach", "in", "return", "break", "continue", "import", "and", "or", "not",
}
BLOCK_STARTERS = {"if", "else", "loops", "foreach", "function"}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def tea_repr(value: Any) -> str:
    """Tea-flavoured rendering used by print and typel.to_string."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[" + ", ".join(_inner_repr(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_inner_repr(k)}: {_inner_repr(v)}" for k, v in value.items()) + "}"
    return str(value)


def _inner_repr(value: Any) -> str:
    return f'"{value}"' if isinstance(value, str) else tea_repr(value)


# ==========================================
# Source splitter: comments, then logical statements
# ==========================================
def strip_comment(line: str) -> str:
    """Remove // comments while ignoring // inside string literals."""
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        else:
            if ch in "'\"":
                quote = ch
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                return line[:i]
    return line


def split_statements(lines: list[str]) -> list[tuple[str, int]]:
    """Turn source lines into (statement, line_number) pairs.

    Handles several statements per line, statements spanning lines, '} else {'
    on one line, and map literals whose braces must NOT open a block.
    """
    out: list[tuple[str, int]] = []
    buf: list[str] = []
    start_line = 1
    started = False
    paren = square = brace = 0

    def flush(suffix: str = "") -> None:
        nonlocal buf, started
        body = "".join(buf).strip()
        buf = []
        started = False
        if not body:
            return
        text = body + (" " + suffix if suffix == "{" else suffix)
        out.append((text, start_line))

    for idx, raw in enumerate(lines):
        line_no = idx + 1
        line = strip_comment(raw)
        quote: str | None = None
        escaped = False
        for ch in line:
            if ch == "\n":
                ch = " "                      # Tea strings never span lines
            if not started and ch.strip():
                start_line = line_no
                started = True
            if quote is not None:
                buf.append(ch)
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                continue
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "[":
                square += 1
            elif ch == "]":
                square = max(0, square - 1)
            elif ch == "{":
                head = re.match(r"^\s*([A-Za-z_]\w*)", "".join(buf))
                is_block = (paren == 0 and square == 0 and brace == 0
                            and head is not None and head.group(1) in BLOCK_STARTERS)
                if is_block:
                    flush("{")
                    continue
                brace += 1
            elif ch == "}":
                if brace > 0:
                    brace -= 1
                else:
                    flush()          # a statement missing its ';'
                    out.append(("}", line_no))
                    continue
            elif ch == ";" and paren == 0 and square == 0 and brace == 0:
                flush(";")
                continue
            buf.append(ch)
        if quote is None:
            buf.append(" ")
    flush()
    return out


# ==========================================
# Standard libraries
# ==========================================
class IOLibrary:
    @staticmethod
    def print_msg(msg: Any) -> str:
        text = tea_repr(msg)
        print(f"[IOL] {text}")
        return text

    @staticmethod
    def write(msg: Any) -> str:
        """Print without a trailing newline."""
        text = tea_repr(msg)
        sys.stdout.write(text)
        sys.stdout.flush()
        return text

    @staticmethod
    def read_input(prompt: Any = "") -> str:
        return input(tea_repr(prompt))

    @staticmethod
    def read_number(prompt: Any = "") -> float:
        raw = input(tea_repr(prompt)).strip()
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError as exc:
                raise ValueError(f"'{raw}' is not a number") from exc


class MathLibrary:
    @staticmethod
    def sqrt(x: Any) -> float:
        val = float(x)
        if val < 0:
            raise ValueError("cannot calculate square root of a negative number")
        return math.sqrt(val)

    @staticmethod
    def power(base: Any, exp: Any) -> float:
        return math.pow(float(base), float(exp))

    @staticmethod
    def absolute(x: Any) -> float:
        return abs(float(x))

    @staticmethod
    def floor(x: Any) -> int:
        return math.floor(float(x))

    @staticmethod
    def ceil(x: Any) -> int:
        return math.ceil(float(x))

    @staticmethod
    def round(x: Any, digits: Any = 0) -> float:
        return round(float(x), int(digits))

    @staticmethod
    def min(*args: Any) -> float:
        vals = args[0] if len(args) == 1 and isinstance(args[0], list) else args
        if not vals:
            raise ValueError("min() needs at least one value")
        return min(vals)

    @staticmethod
    def max(*args: Any) -> float:
        vals = args[0] if len(args) == 1 and isinstance(args[0], list) else args
        if not vals:
            raise ValueError("max() needs at least one value")
        return max(vals)

    @staticmethod
    def sign(x: Any) -> int:
        v = float(x)
        return (v > 0) - (v < 0)

    @staticmethod
    def clamp(x: Any, low: Any, high: Any) -> Any:
        if not all(is_number(v) for v in (x, low, high)):
            raise ValueError("clamp() requires numbers")
        if low > high:
            raise ValueError("clamp() needs low <= high")
        return max(low, min(x, high))

    @staticmethod
    def sin(x: Any) -> float:
        return math.sin(float(x))

    @staticmethod
    def cos(x: Any) -> float:
        return math.cos(float(x))

    @staticmethod
    def tan(x: Any) -> float:
        return math.tan(float(x))

    @staticmethod
    def log(x: Any, base: Any = math.e) -> float:
        return math.log(float(x), float(base))

    @staticmethod
    def exp(x: Any) -> float:
        return math.exp(float(x))

    @staticmethod
    def gcd(a: Any, b: Any) -> int:
        return math.gcd(int(a), int(b))

    @staticmethod
    def hypot(a: Any, b: Any) -> float:
        return math.hypot(float(a), float(b))

    @staticmethod
    def random() -> float:
        return random.random()

    @staticmethod
    def random_int(low: Any, high: Any) -> int:
        return random.randint(int(low), int(high))

    @staticmethod
    def pi() -> float:
        return math.pi

    @staticmethod
    def e() -> float:
        return math.e

    @staticmethod
    def is_even(x: Any) -> bool:
        return int(x) % 2 == 0

    @staticmethod
    def is_odd(x: Any) -> bool:
        return int(x) % 2 != 0


class StringLibrary:
    @staticmethod
    def len(s: Any) -> int:
        return len(str(s))

    @staticmethod
    def upper(s: Any) -> str:
        return str(s).upper()

    @staticmethod
    def lower(s: Any) -> str:
        return str(s).lower()

    @staticmethod
    def title(s: Any) -> str:
        return str(s).title()

    @staticmethod
    def strip(s: Any, chars: Any = None) -> str:
        return str(s).strip() if chars is None else str(s).strip(str(chars))

    @staticmethod
    def split(s: Any, sep: Any = None) -> list:
        return str(s).split() if sep is None else str(s).split(str(sep))

    @staticmethod
    def join(parts: Any, sep: Any = "") -> str:
        if not isinstance(parts, list):
            raise ValueError("join() expects an array as its first argument")
        return str(sep).join(tea_repr(p) for p in parts)

    @staticmethod
    def replace(s: Any, old: Any, new: Any) -> str:
        return str(s).replace(str(old), str(new))

    @staticmethod
    def contains(s: Any, sub: Any) -> bool:
        return str(sub) in str(s)

    @staticmethod
    def starts_with(s: Any, prefix: Any) -> bool:
        return str(s).startswith(str(prefix))

    @staticmethod
    def ends_with(s: Any, suffix: Any) -> bool:
        return str(s).endswith(str(suffix))

    @staticmethod
    def find(s: Any, sub: Any) -> int:
        return str(s).find(str(sub))

    @staticmethod
    def count(s: Any, sub: Any) -> int:
        return str(s).count(str(sub))

    @staticmethod
    def slice(s: Any, start: Any, end: Any = None) -> str:
        return str(s)[int(start):] if end is None else str(s)[int(start):int(end)]

    @staticmethod
    def char_at(s: Any, i: Any) -> str:
        text = str(s)
        idx = int(i)
        if idx < 0:
            idx += len(text)
        if not 0 <= idx < len(text):
            raise ValueError(f"character index {i} out of range")
        return text[idx]

    @staticmethod
    def repeat(s: Any, n: Any) -> str:
        count = int(n)
        if count < 0 or len(str(s)) * count > MAX_STR_CHARS:
            raise ValueError("repeat() result is too large")
        return str(s) * count

    @staticmethod
    def reverse(s: Any) -> str:
        return str(s)[::-1]

    @staticmethod
    def pad_left(s: Any, width: Any, fill: Any = " ") -> str:
        return str(s).rjust(int(width), str(fill)[:1] or " ")

    @staticmethod
    def pad_right(s: Any, width: Any, fill: Any = " ") -> str:
        return str(s).ljust(int(width), str(fill)[:1] or " ")

    @staticmethod
    def to_number(s: Any) -> float:
        text = str(s).strip()
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError as exc:
                raise ValueError(f"'{text}' is not a number") from exc

    @staticmethod
    def is_digit(s: Any) -> bool:
        return str(s).isdigit()

    @staticmethod
    def is_alpha(s: Any) -> bool:
        return str(s).isalpha()


class ArrayLibrary:
    @staticmethod
    def _need(a: Any, fname: str) -> list:
        if not isinstance(a, list):
            raise ValueError(f"{fname}() expects an array")
        return a

    @staticmethod
    def len(a: Any) -> int:
        return len(ArrayLibrary._need(a, "len"))

    @staticmethod
    def push(a: Any, value: Any) -> list:
        arr = ArrayLibrary._need(a, "push")
        if len(arr) >= MAX_LIST_ITEMS:
            raise ValueError("array is full")
        arr.append(value)
        return arr

    @staticmethod
    def pop(a: Any, index: Any = -1) -> Any:
        arr = ArrayLibrary._need(a, "pop")
        if not arr:
            raise ValueError("pop() from an empty array")
        return arr.pop(int(index))

    @staticmethod
    def insert(a: Any, index: Any, value: Any) -> list:
        arr = ArrayLibrary._need(a, "insert")
        arr.insert(int(index), value)
        return arr

    @staticmethod
    def remove_at(a: Any, index: Any) -> Any:
        arr = ArrayLibrary._need(a, "remove_at")
        return arr.pop(int(index))

    @staticmethod
    def remove(a: Any, value: Any) -> bool:
        arr = ArrayLibrary._need(a, "remove")
        if value in arr:
            arr.remove(value)
            return True
        return False

    @staticmethod
    def clear(a: Any) -> list:
        arr = ArrayLibrary._need(a, "clear")
        arr.clear()
        return arr

    @staticmethod
    def index_of(a: Any, value: Any) -> int:
        arr = ArrayLibrary._need(a, "index_of")
        return arr.index(value) if value in arr else -1

    @staticmethod
    def contains(a: Any, value: Any) -> bool:
        return value in ArrayLibrary._need(a, "contains")

    @staticmethod
    def count(a: Any, value: Any) -> int:
        return ArrayLibrary._need(a, "count").count(value)

    @staticmethod
    def slice(a: Any, start: Any, end: Any = None) -> list:
        arr = ArrayLibrary._need(a, "slice")
        return arr[int(start):] if end is None else arr[int(start):int(end)]

    @staticmethod
    def reverse(a: Any) -> list:
        arr = ArrayLibrary._need(a, "reverse")
        arr.reverse()
        return arr

    @staticmethod
    def reversed(a: Any) -> list:
        return list(reversed(ArrayLibrary._need(a, "reversed")))

    @staticmethod
    def sort(a: Any, descending: Any = False) -> list:
        arr = ArrayLibrary._need(a, "sort")
        arr.sort(reverse=bool(descending))
        return arr

    @staticmethod
    def sorted(a: Any, descending: Any = False) -> list:
        return sorted(ArrayLibrary._need(a, "sorted"), reverse=bool(descending))

    @staticmethod
    def unique(a: Any) -> list:
        seen: list = []
        for item in ArrayLibrary._need(a, "unique"):
            if item not in seen:
                seen.append(item)
        return seen

    @staticmethod
    def sum(a: Any) -> float:
        return sum(ArrayLibrary._need(a, "sum"))

    @staticmethod
    def min(a: Any) -> Any:
        arr = ArrayLibrary._need(a, "min")
        if not arr:
            raise ValueError("min() of an empty array")
        return min(arr)

    @staticmethod
    def max(a: Any) -> Any:
        arr = ArrayLibrary._need(a, "max")
        if not arr:
            raise ValueError("max() of an empty array")
        return max(arr)

    @staticmethod
    def join(a: Any, sep: Any = "") -> str:
        return str(sep).join(tea_repr(v) for v in ArrayLibrary._need(a, "join"))

    @staticmethod
    def first(a: Any) -> Any:
        arr = ArrayLibrary._need(a, "first")
        if not arr:
            raise ValueError("first() of an empty array")
        return arr[0]

    @staticmethod
    def last(a: Any) -> Any:
        arr = ArrayLibrary._need(a, "last")
        if not arr:
            raise ValueError("last() of an empty array")
        return arr[-1]

    @staticmethod
    def copy(a: Any) -> list:
        return list(ArrayLibrary._need(a, "copy"))

    @staticmethod
    def deep_copy(a: Any) -> list:
        return copy.deepcopy(ArrayLibrary._need(a, "deep_copy"))

    @staticmethod
    def range(start: Any, stop: Any = None, step: Any = 1) -> list:
        a, b, s = (0, int(start), int(step)) if stop is None else (int(start), int(stop), int(step))
        if s == 0:
            raise ValueError("range() step cannot be zero")
        size = max(0, (b - a + (s - (1 if s > 0 else -1))) // s)
        if size > MAX_LIST_ITEMS:
            raise ValueError("range() result is too large")
        return list(range(a, b, s))


class TypeLibrary:
    @staticmethod
    def type_of(v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, int):
            return "integer"
        if isinstance(v, float):
            return "float"
        if isinstance(v, str):
            return "string"
        if isinstance(v, list):
            return "array"
        if isinstance(v, dict):
            return "map"
        return "unknown"

    @staticmethod
    def len(v: Any) -> int:
        if isinstance(v, (str, list, dict)):
            return len(v)
        raise ValueError("len() expects a string, array, or map")

    @staticmethod
    def is_number(v: Any) -> bool:
        return is_number(v)

    @staticmethod
    def is_string(v: Any) -> bool:
        return isinstance(v, str)

    @staticmethod
    def is_array(v: Any) -> bool:
        return isinstance(v, list)

    @staticmethod
    def is_map(v: Any) -> bool:
        return isinstance(v, dict)

    @staticmethod
    def is_bool(v: Any) -> bool:
        return isinstance(v, bool)

    @staticmethod
    def is_null(v: Any) -> bool:
        return v is None

    @staticmethod
    def to_string(v: Any) -> str:
        return tea_repr(v)

    @staticmethod
    def to_number(v: Any) -> float:
        if is_number(v):
            return v
        return StringLibrary.to_number(v)

    @staticmethod
    def to_array(v: Any) -> list:
        if isinstance(v, list):
            return list(v)
        if isinstance(v, str):
            return list(v)
        if isinstance(v, dict):
            return list(v.keys())
        raise ValueError("to_array() expects a string, array, or map")

    @staticmethod
    def keys(m: Any) -> list:
        if not isinstance(m, dict):
            raise ValueError("keys() expects a map")
        return list(m.keys())

    @staticmethod
    def values(m: Any) -> list:
        if not isinstance(m, dict):
            raise ValueError("values() expects a map")
        return list(m.values())

    @staticmethod
    def has(m: Any, key: Any) -> bool:
        if not isinstance(m, dict):
            raise ValueError("has() expects a map")
        return key in m

    @staticmethod
    def remove_key(m: Any, key: Any) -> bool:
        if not isinstance(m, dict):
            raise ValueError("remove_key() expects a map")
        return m.pop(key, _MISSING) is not _MISSING


_MISSING = object()


class TimeLibrary:
    @staticmethod
    def now() -> float:
        return time.time()

    @staticmethod
    def ticks_ms() -> int:
        return int(time.monotonic() * 1000)

    @staticmethod
    def sleep(seconds: Any) -> float:
        secs = float(seconds)
        if secs < 0:
            raise ValueError("sleep() needs a non-negative duration")
        if secs > MAX_SLEEP_SECONDS:
            raise ValueError(f"sleep() is capped at {MAX_SLEEP_SECONDS} seconds")
        time.sleep(secs)
        return secs


class AILibrary:
    @staticmethod
    def process_logic(val: Any) -> str:
        return f"[AILL] Processed: {tea_repr(val)}"

    @staticmethod
    def evaluate_condition(flag: Any) -> bool:
        if isinstance(flag, str) and flag.strip().lower() in ("false", "no", "off", "0", ""):
            return False
        return bool(flag)

class GraphicLibrary:
    """Lazy Tkinter graphics. Tk is imported only when actually used."""

    _tk = None
    _root = None
    _canvas = None
    _width = 400
    _height = 400
    _closed = False

    @staticmethod
    def _load_tk():
        if GraphicLibrary._tk is None:
            try:
                import tkinter as tk
            except ImportError as exc:
                raise RuntimeError("graphicl requires tkinter, which is not available") from exc
            GraphicLibrary._tk = tk
        return GraphicLibrary._tk

    @staticmethod
    def _init_window() -> None:
        if GraphicLibrary._root is not None and not GraphicLibrary._closed:
            return
        tk = GraphicLibrary._load_tk()
        try:
            GraphicLibrary._closed = False
            GraphicLibrary._root = tk.Tk()
            GraphicLibrary._root.title("Tea Language Graphic Engine")
            GraphicLibrary._canvas = tk.Canvas(
                GraphicLibrary._root, width=GraphicLibrary._width,
                height=GraphicLibrary._height, bg="black", highlightthickness=0,
            )
            GraphicLibrary._canvas.pack()
            GraphicLibrary._root.protocol("WM_DELETE_WINDOW", GraphicLibrary._on_close)
            GraphicLibrary._root.update_idletasks()
        except Exception as exc:
            GraphicLibrary._root = None
            GraphicLibrary._canvas = None
            raise RuntimeError(f"graphicl failed to initialize Tkinter window: {exc}") from exc

    @staticmethod
    def _ready():
        GraphicLibrary._init_window()
        if GraphicLibrary._closed or GraphicLibrary._canvas is None:
            raise RuntimeError("graphicl window is closed")
        return GraphicLibrary._canvas

    @staticmethod
    def _refresh() -> None:
        GraphicLibrary._root.update_idletasks()
        GraphicLibrary._root.update()

    @staticmethod
    def _on_close() -> None:
        GraphicLibrary._closed = True
        try:
            if GraphicLibrary._root is not None:
                GraphicLibrary._root.destroy()
        finally:
            GraphicLibrary._root = None
            GraphicLibrary._canvas = None

    @staticmethod
    def window(width: Any, height: Any) -> str:
        w, h = int(width), int(height)
        if w <= 0 or h <= 0:
            raise ValueError("window width and height must be positive")
        GraphicLibrary._width, GraphicLibrary._height = w, h
        GraphicLibrary._init_window()
        try:
            GraphicLibrary._canvas.config(width=w, height=h)
            GraphicLibrary._refresh()
        except Exception as exc:
            raise RuntimeError(f"graphicl window failed: {exc}") from exc
        return "[GRAPHICL] Window initialized successfully."

    @staticmethod
    def draw_rect(x: Any, y: Any, w: Any, h: Any, color: Any) -> str:
        canvas = GraphicLibrary._ready()
        x, y, w, h = float(x), float(y), float(w), float(h)
        if w < 0 or h < 0:
            raise ValueError("rectangle width and height must be non-negative")
        try:
            canvas.create_rectangle(x, y, x + w, y + h, fill=str(color), outline="")
            GraphicLibrary._refresh()
        except Exception as exc:
            raise RuntimeError(f"graphicl draw_rect failed: {exc}") from exc
        return "[GRAPHICL] Rectangle drawn."

    @staticmethod
    def draw_circle(x: Any, y: Any, r: Any, color: Any) -> str:
        canvas = GraphicLibrary._ready()
        x, y, r = float(x), float(y), float(r)
        if r < 0:
            raise ValueError("circle radius must be non-negative")
        try:
            canvas.create_oval(x - r, y - r, x + r, y + r, fill=str(color), outline="")
            GraphicLibrary._refresh()
        except Exception as exc:
            raise RuntimeError(f"graphicl draw_circle failed: {exc}") from exc
        return "[GRAPHICL] Circle drawn."

    @staticmethod
    def draw_line(x1: Any, y1: Any, x2: Any, y2: Any, color: Any = "white", width: Any = 1) -> str:
        canvas = GraphicLibrary._ready()
        try:
            canvas.create_line(float(x1), float(y1), float(x2), float(y2),
                               fill=str(color), width=float(width))
            GraphicLibrary._refresh()
        except Exception as exc:
            raise RuntimeError(f"graphicl draw_line failed: {exc}") from exc
        return "[GRAPHICL] Line drawn."

    @staticmethod
    def draw_text(x: Any, y: Any, text: Any, color: Any = "white", size: Any = 14) -> str:
        canvas = GraphicLibrary._ready()
        try:
            canvas.create_text(float(x), float(y), text=tea_repr(text), fill=str(color),
                               font=("Helvetica", int(size)), anchor="nw")
            GraphicLibrary._refresh()
        except Exception as exc:
            raise RuntimeError(f"graphicl draw_text failed: {exc}") from exc
        return "[GRAPHICL] Text drawn."

    @staticmethod
    def clear() -> str:
        if GraphicLibrary._canvas is not None and not GraphicLibrary._closed:
            try:
                GraphicLibrary._canvas.delete("all")
                GraphicLibrary._root.update_idletasks()
            except Exception as exc:
                raise RuntimeError(f"graphicl clear failed: {exc}") from exc
        return "[GRAPHICL] Canvas cleared."

    @staticmethod
    def close() -> str:
        GraphicLibrary._on_close()
        return "[GRAPHICL] Window closed."


class GameLibrary:
    """Lazy pygame wrapper with a clearer lifecycle."""

    _pygame = None
    _initialized = False
    _screen = None
    _clock = None
    _running = False

    @staticmethod
    def _load_pygame():
        if GameLibrary._pygame is None:
            try:
                import pygame
            except ImportError as exc:
                raise RuntimeError("gamel requires 'pygame'. Run 'pip install pygame'.") from exc
            GameLibrary._pygame = pygame
        return GameLibrary._pygame

    @staticmethod
    def init() -> str:
        pygame = GameLibrary._load_pygame()
        try:
            pygame.init()
            GameLibrary._initialized = True
            GameLibrary._clock = pygame.time.Clock()
            return "[GAMEL] Pygame subsystem initialized successfully."
        except Exception as exc:
            raise RuntimeError(f"gamel initialization error: {exc}") from exc

    @staticmethod
    def set_mode(width: Any, height: Any, title: Any = "Tea Game") -> str:
        if not GameLibrary._initialized:
            GameLibrary.init()
        pygame = GameLibrary._load_pygame()
        w, h = int(width), int(height)
        if w <= 0 or h <= 0:
            raise ValueError("display width and height must be positive")
        try:
            GameLibrary._screen = pygame.display.set_mode((w, h))
            pygame.display.set_caption(tea_repr(title))
            GameLibrary._running = True
            return f"[GAMEL] Display mode set to {w}x{h}."
        except Exception as exc:
            raise RuntimeError(f"gamel set_mode failed: {exc}") from exc

    @staticmethod
    def clear_screen(r: Any = 0, g: Any = 0, b: Any = 0) -> str:
        if GameLibrary._screen is None or not GameLibrary._running:
            raise RuntimeError("gamel screen is not running; call gamel.set_mode(w, h) first")
        rgb = tuple(int(v) for v in (r, g, b))
        if any(v < 0 or v > 255 for v in rgb):
            raise ValueError("RGB values must be between 0 and 255")
        try:
            GameLibrary._screen.fill(rgb)
            return "[GAMEL] Screen cleared."
        except Exception as exc:
            raise RuntimeError(f"gamel clear_screen failed: {exc}") from exc

    @staticmethod
    def update() -> bool:
        if not GameLibrary._running or GameLibrary._screen is None:
            return False
        pygame = GameLibrary._load_pygame()
        try:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    GameLibrary.quit()
                    return False
            pygame.display.flip()
            if GameLibrary._clock is not None:
                GameLibrary._clock.tick(60)
            return GameLibrary._running
        except Exception as exc:
            raise RuntimeError(f"gamel update failed: {exc}") from exc

    @staticmethod
    def is_running() -> bool:
        return GameLibrary._running

    @staticmethod
    def quit() -> str:
        if GameLibrary._pygame is not None:
            try:
                GameLibrary._pygame.quit()
            except Exception:
                pass
        GameLibrary._initialized = False
        GameLibrary._screen = None
        GameLibrary._clock = None
        GameLibrary._running = False
        return "[GAMEL] Pygame subsystem shut down."


LIBRARIES = {
    "iol": IOLibrary,
    "mathl": MathLibrary,
    "stringl": StringLibrary,
    "arrayl": ArrayLibrary,
    "typel": TypeLibrary,
    "timel": TimeLibrary,
    "aill": AILibrary,
    "graphicl": GraphicLibrary,
    "gamel": GameLibrary,
}


def _public_api(cls) -> set[str]:
    """Only public callables defined on the library class itself are callable
    from Tea. Dunders, private helpers and inherited object methods never leak."""
    return {
        name for name, value in vars(cls).items()
        if not name.startswith("_") and callable(getattr(value, "__func__", value))
    }


LIBRARY_APIS = {name: _public_api(cls) for name, cls in LIBRARIES.items()}

# ==========================================
# Compiler + VM
# ==========================================
class TeaCompiler:
    def __init__(self, max_steps: int = 5_000_000, max_recursion: int = 200,
                 max_loop_iterations: int = 1_000_000, allow_redefine: bool = False,
                 quiet: bool = False):
        self.errors: list[TeaSyntaxError] = []
        self.imported_libs: set[str] = set()
        self.functions: dict[str, dict[str, Any]] = {}
        self.libraries = LIBRARIES
        self.current_file = "app.tea"
        self.source_lines: list[str] = []
        self.max_steps = max_steps
        self.max_recursion = max_recursion
        self.max_loop_iterations = max_loop_iterations
        self.allow_redefine = allow_redefine
        self.quiet = quiet
        self.echo_expressions = False
        self._bootstrap_index: int | None = None
        self.scopes: list[dict[str, Any]] = [{}]
        self.steps = 0
        self.call_depth = 0
        self._scope_floor = 0

    # ---------------- source / parsing ----------------
    def compile_source(self, file_path: str | os.PathLike[str]) -> list[tuple] | None:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        try:
            with path.open("r", encoding="utf-8") as f:
                lines = [line.expandtabs(4) for line in f.readlines()]
        except OSError as exc:
            raise TeaSyntaxError(f"Cannot read source file '{path}': {exc}", None, path.name) from exc
        except UnicodeDecodeError as exc:
            raise TeaSyntaxError(f"Source file '{path}' is not valid UTF-8: {exc}", None, path.name) from exc
        return self.compile_lines(lines, name=path.name)

    def compile_string(self, source: str, name: str = "<string>") -> list[tuple] | None:
        lines = [line.expandtabs(4) for line in source.splitlines(keepends=True)]
        return self.compile_lines(lines, name=name)

    def compile_lines(self, lines: list[str], name: str = "app.tea") -> list[tuple] | None:
        self.errors = []
        self.imported_libs = set()
        self.functions = {}
        self._bootstrap_index = None
        self.current_file = name
        self.source_lines = lines

        if not self.quiet:
            print(f"Building: {self.current_file} ...")
        stmts = split_statements(lines)

        if not stmts:
            self._err(f"Empty source file. Expected '{BOOTSTRAP}'", 1)
        elif stmts[0][0] != BOOTSTRAP:
            self._err(f"Invalid bootstrap. Expected '{BOOTSTRAP}', found '{stmts[0][0]}'", stmts[0][1])
        self._bootstrap_index = 0 if stmts and stmts[0][0] == BOOTSTRAP else None

        # Pre-collect imports so a function body may use a library imported later.
        for text, _ln in stmts:
            m = re.match(r"^<\s*import\s+([A-Za-z_]\w*)\s*>;$", text)
            if m and m.group(1) in self.libraries:
                self.imported_libs.add(m.group(1))

        try:
            bytecode, _, _ = self.parse_block(stmts, 0, top_level=True)
        except TeaSyntaxError as exc:
            self.errors.append(exc)
        except RecursionError:
            self._err("Source is nested too deeply for the parser to handle", 1)

        if self.errors:
            if not self.quiet:
                print(f"\n[Compilation Failed] {len(self.errors)} error(s):")
                for err in self.errors:
                    print(format_error(err, self.source_lines))
            return None

        if not self.quiet:
            print("Compilation successful.")
        return bytecode

    def _err(self, message: str, line_num: int) -> None:
        self.errors.append(TeaSyntaxError(message, line_num, self.current_file))

    def _check_identifier(self, name: str, line_num: int, kind: str = "identifier") -> bool:
        if not IDENT_RE.match(name):
            self._err(f"Invalid {kind} '{name}'", line_num)
            return False
        if name in RESERVED:
            self._err(f"Reserved word '{name}' cannot be used as a {kind}", line_num)
            return False
        if name.startswith("__"):
            self._err(f"{kind} '{name}' may not start with '__'", line_num)
            return False
        return True

    def _parse_if(self, stmts, i, line_num, cond_text, *, in_function, loop_depth, block_depth):
        cond = self.compile_expr(cond_text, line_num)
        if_body, next_i, _ = self.parse_block(
            stmts, i + 1, in_function=in_function, loop_depth=loop_depth,
            opener_line=line_num, block_depth=block_depth + 1,
        )
        else_body: list[tuple] = []
        if next_i < len(stmts):
            text, ln = stmts[next_i]
            if text == "else {":
                else_body, next_i, _ = self.parse_block(
                    stmts, next_i + 1, in_function=in_function, loop_depth=loop_depth,
                    opener_line=ln, block_depth=block_depth + 1,
                )
            else:
                m = re.match(r"^else\s+if\s*\((.*)\)\s*\{$", text)
                if m:
                    nested, next_i = self._parse_if(
                        stmts, next_i, ln, m.group(1).strip(),
                        in_function=in_function, loop_depth=loop_depth, block_depth=block_depth + 1,
                    )
                    else_body = [nested]
                elif text.startswith("else"):
                    self._err("Expected 'else {' or 'else if (...) {'", ln)
                    next_i += 1
        return ("IF", cond, if_body, else_body, line_num), next_i

    def parse_block(self, stmts, start: int, *, top_level: bool = False,
                    in_function: bool = False, loop_depth: int = 0,
                    opener_line: int | None = None,
                    block_depth: int = 0) -> tuple[list[tuple], int, bool]:
        if block_depth > MAX_BLOCK_DEPTH:
            raise TeaSyntaxError(f"Maximum block nesting depth exceeded ({MAX_BLOCK_DEPTH})",
                                 opener_line, self.current_file)
        bytecode: list[tuple] = []
        i = start
        n = len(stmts)

        while i < n:
            text, line_num = stmts[i]

            if text == "}":
                if top_level:
                    self._err("Unexpected '}' at top level", line_num)
                    i += 1
                    continue
                return bytecode, i + 1, True

            if text.startswith("else"):
                self._err("'else' without a matching 'if'", line_num)
                i += 1
                continue

            if text == BOOTSTRAP:
                if self._bootstrap_index == i:
                    bytecode.append(("BOOTSTRAP", line_num))
                else:
                    self._err(f"'{BOOTSTRAP}' is only allowed as the first statement", line_num)
                i += 1
                continue

            # Import
            m = re.match(r"^<\s*import\s+([A-Za-z_]\w*)\s*>;$", text)
            if m:
                if not top_level:
                    self._err("Imports must be at top level", line_num)
                lib = m.group(1)
                if lib not in self.libraries:
                    self._err(f"Unknown library '{lib}'.{suggest(lib, self.libraries)} "
                              f"Known: {', '.join(sorted(self.libraries))}", line_num)
                else:
                    self.imported_libs.add(lib)
                    bytecode.append(("IMPORT", lib, line_num))
                i += 1
                continue

            # Function definition (supports default arguments)
            m = re.match(r"^function\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*\{$", text)
            if m:
                name = m.group(1)
                valid = self._check_identifier(name, line_num, "function name")
                if not top_level:
                    self._err("Functions must be defined at top level", line_num)
                params, defaults = self._parse_params(m.group(2), line_num)
                body, i, _ = self.parse_block(
                    stmts, i + 1, in_function=True, loop_depth=0,
                    opener_line=line_num, block_depth=block_depth + 1,
                )
                if valid and top_level:
                    if name in self.functions and not self.allow_redefine:
                        self._err(f"Duplicate function '{name}'", line_num)
                    else:
                        self.functions[name] = {"params": params, "defaults": defaults,
                                                "body": body, "line": line_num}
                bytecode.append(("FUNC_DEF", name, line_num))
                continue

            # Core print
            m = re.match(r"^iol\.print\s*\((.*)\);$", text)
            if m:
                bytecode.append(("PRINT", self.compile_expr(m.group(1).strip(), line_num), line_num))
                i += 1
                continue

            # Declaration
            m = re.match(r"^variable\s+([A-Za-z_]\w*)\s*=\s*(.+);$", text, re.DOTALL)
            if m:
                self._check_identifier(m.group(1), line_num, "variable name")
                bytecode.append(("STORE", m.group(1), self.compile_expr(m.group(2).strip(), line_num), line_num))
                i += 1
                continue

            # Return
            m = re.match(r"^return\b\s*(.*);$", text, re.DOTALL)
            if m:
                if not in_function:
                    self._err("'return' is only allowed inside a function", line_num)
                expr_text = m.group(1).strip()
                bytecode.append(("RETURN", self.compile_expr(expr_text, line_num) if expr_text else None, line_num))
                i += 1
                continue

            if text in ("break;", "continue;"):
                if loop_depth <= 0:
                    self._err(f"'{text[:-1]}' is only allowed inside a loop", line_num)
                bytecode.append(("BREAK" if text == "break;" else "CONTINUE", line_num))
                i += 1
                continue

            # If / else
            m = re.match(r"^if\s*\((.*)\)\s*\{$", text)
            if m:
                instr, i = self._parse_if(stmts, i, line_num, m.group(1).strip(),
                                          in_function=in_function, loop_depth=loop_depth,
                                          block_depth=block_depth)
                bytecode.append(instr)
                continue

            # While-style loop
            m = re.match(r"^loops\s*\((.*)\)\s*\{$", text)
            if m:
                if loop_depth + 1 > MAX_LOOP_DEPTH:
                    self._err(f"Maximum loop nesting depth exceeded ({MAX_LOOP_DEPTH})", line_num)
                cond = self.compile_expr(m.group(1).strip(), line_num)
                body, i, _ = self.parse_block(
                    stmts, i + 1, in_function=in_function, loop_depth=loop_depth + 1,
                    opener_line=line_num, block_depth=block_depth + 1,
                )
                bytecode.append(("LOOPS", cond, body, line_num))
                continue

            # Foreach loop
            m = re.match(r"^foreach\s*\(\s*([A-Za-z_]\w*)\s+in\s+(.*)\)\s*\{$", text)
            if m:
                if loop_depth + 1 > MAX_LOOP_DEPTH:
                    self._err(f"Maximum loop nesting depth exceeded ({MAX_LOOP_DEPTH})", line_num)
                var = m.group(1)
                self._check_identifier(var, line_num, "loop variable")
                iterable = self.compile_expr(m.group(2).strip(), line_num)
                body, i, _ = self.parse_block(
                    stmts, i + 1, in_function=in_function, loop_depth=loop_depth + 1,
                    opener_line=line_num, block_depth=block_depth + 1,
                )
                bytecode.append(("FOREACH", var, iterable, body, line_num))
                continue

            # Assignment / indexed assignment / call statement
            if text.endswith(";"):
                if self._parse_simple_statement(text[:-1].strip(), line_num, bytecode):
                    i += 1
                    continue

            self._err(f"Unknown syntax: '{text}'", line_num)
            i += 1

        if not top_level:
            opener = opener_line if opener_line is not None else start
            self._err(f"Unclosed block opened on line {opener}; expected '}}'", opener)
            return bytecode, i, False
        return bytecode, i, True

    def _parse_params(self, text: str, line_num: int) -> tuple[list[str], list[ast.AST]]:
        """Parse 'a, b = 10' into parameter names and compiled default expressions."""
        text = text.strip()
        if not text:
            return [], []
        try:
            sig = ast.parse(f"def _({text}): pass", mode="exec").body[0].args
        except SyntaxError as exc:
            self._err(f"Invalid parameter list '{text}': {exc.msg}", line_num)
            return [], []
        if sig.vararg or sig.kwarg or sig.kwonlyargs or sig.posonlyargs:
            self._err("Only plain parameters with optional defaults are supported", line_num)
            return [], []
        params = [a.arg for a in sig.args]
        seen: set[str] = set()
        for p in params:
            self._check_identifier(p, line_num, "parameter")
            if p in seen:
                self._err(f"Duplicate parameter '{p}'", line_num)
            seen.add(p)
        defaults = [self.compile_expr(ast.unparse(d), line_num) for d in sig.defaults]
        return params, defaults

    def _parse_simple_statement(self, body: str, line_num: int, bytecode: list[tuple]) -> bool:
        """Handle 'x = ...', 'arr[i] = ...', 'm[k] = ...', and bare calls."""
        if not body:
            return False
        try:
            tree = ast.parse(body, mode="exec")
        except (SyntaxError, RecursionError):
            return False
        if len(tree.body) != 1:
            return False
        node = tree.body[0]

        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                self._err("Chained assignment is not supported", line_num)
                return True
            target = node.targets[0]
            value = self.compile_expr(ast.unparse(node.value), line_num)
            if isinstance(target, ast.Name):
                self._check_identifier(target.id, line_num, "variable name")
                bytecode.append(("ASSIGN", target.id, value, line_num))
                return True
            if isinstance(target, ast.Subscript) and not isinstance(target.slice, ast.Slice):
                container = self.compile_expr(ast.unparse(target.value), line_num)
                index = self.compile_expr(ast.unparse(target.slice), line_num)
                bytecode.append(("SET_INDEX", container, index, value, line_num))
                return True
            self._err("Invalid assignment target", line_num)
            return True

        if isinstance(node, ast.Expr):
            expr = self.compile_expr(body, line_num)
            if isinstance(expr, ast.Call):
                bytecode.append(("CALL_EXPR", expr, line_num))
                return True
            if self.echo_expressions:      # REPL only: echo a bare expression
                bytecode.append(("PRINT", expr, line_num))
                return True
            return False
        return False

    # ---------------- expression compilation ----------------
    def compile_expr(self, text: str, line_num: int) -> ast.AST:
        if not text:
            self._err("Empty expression", line_num)
            return ast.Constant(value=None)
        shown = text if len(text) <= MAX_ERR_TEXT else text[:MAX_ERR_TEXT] + "..."
        try:
            tree = ast.parse(text, mode="eval")
            self._validate_expr(tree.body, line_num, 0)
            return tree.body
        except TeaSyntaxError as exc:
            self.errors.append(exc)
        except SyntaxError as exc:
            self._err(f"Invalid expression '{shown}': {exc.msg}", line_num)
        except RecursionError:
            self._err(f"Expression '{shown}' is nested too deeply", line_num)
        return ast.Constant(value=None)

    def _validate_expr(self, node: ast.AST, line_num: int, depth: int) -> None:
        if depth > MAX_EXPR_DEPTH:
            raise TeaSyntaxError("Expression is nested too deeply", line_num, self.current_file)
        recur = lambda nd: self._validate_expr(nd, line_num, depth + 1)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool)) or node.value is None:
                return
            raise TeaSyntaxError(f"Unsupported literal type: {type(node.value).__name__}",
                                 line_num, self.current_file)

        if isinstance(node, ast.Name):
            if node.id in ("true", "false", "null"):
                return
            if node.id in RESERVED or node.id.startswith("__"):
                raise TeaSyntaxError(f"Invalid name '{node.id}'", line_num, self.current_file)
            return

        if isinstance(node, (ast.List, ast.Tuple)):
            for elt in node.elts:
                recur(elt)
            return

        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if k is None:
                    raise TeaSyntaxError("Map unpacking is not supported", line_num, self.current_file)
                recur(k)
                recur(v)
            return

        if isinstance(node, ast.Subscript):
            recur(node.value)
            recur(node.slice)
            return

        if isinstance(node, ast.Slice):
            for part in (node.lower, node.upper, node.step):
                if part is not None:
                    recur(part)
            return

        if isinstance(node, ast.Call):
            if node.keywords:
                raise TeaSyntaxError("Keyword arguments are not supported", line_num, self.current_file)
            if any(isinstance(a, ast.Starred) for a in node.args):
                raise TeaSyntaxError("Argument unpacking is not supported", line_num, self.current_file)
            if isinstance(node.func, ast.Name):
                if node.func.id in RESERVED or node.func.id.startswith("__"):
                    raise TeaSyntaxError(f"Invalid function name '{node.func.id}'", line_num, self.current_file)
            elif isinstance(node.func, ast.Attribute):
                if not isinstance(node.func.value, ast.Name):
                    raise TeaSyntaxError("Only simple library calls like lib.func(...) are supported",
                                         line_num, self.current_file)
                lib, func = node.func.value.id, node.func.attr
                if lib not in self.libraries:
                    raise TeaSyntaxError(f"Unknown library '{lib}'.{suggest(lib, self.libraries)}",
                                         line_num, self.current_file)
                if lib not in self.imported_libs:
                    raise TeaSyntaxError(f"Library '{lib}' not imported", line_num, self.current_file)
                if func not in LIBRARY_APIS[lib]:
                    raise TeaSyntaxError(f"Unknown or private function {lib}.{func}."
                                         f"{suggest(func, LIBRARY_APIS[lib])}", line_num, self.current_file)
            else:
                raise TeaSyntaxError("Unsupported call target", line_num, self.current_file)
            for arg in node.args:
                recur(arg)
            return

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
                recur(node.operand)
                return
            raise TeaSyntaxError("Unsupported unary operator", line_num, self.current_file)

        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
                recur(node.left)
                recur(node.right)
                return
            raise TeaSyntaxError("Unsupported binary operator", line_num, self.current_file)

        if isinstance(node, ast.BoolOp):
            for value in node.values:
                recur(value)
            return

        if isinstance(node, ast.Compare):
            allowed = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
            if all(isinstance(op, allowed) for op in node.ops):
                recur(node.left)
                for c in node.comparators:
                    recur(c)
                return
            raise TeaSyntaxError("Unsupported comparison operator", line_num, self.current_file)

        raise TeaSyntaxError(
            f"Unsupported expression syntax: {type(node).__name__}. Tea expressions are not Python; "
            "attribute access, comprehensions and lambdas are disabled.", line_num, self.current_file)

    # ---------------- scopes / guards ----------------
    def _tick(self, line_num: int | None = None) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise TeaRuntimeError(f"Execution step limit exceeded ({self.max_steps})", line_num, self.current_file)

    def _visible_names(self) -> set[str]:
        names: set[str] = set()
        for scope in self.scopes:
            names.update(scope)
        return names

    def lookup(self, name: str, line_num: int) -> Any:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise TeaRuntimeError(f"Undefined variable '{name}'.{suggest(name, self._visible_names())}",
                              line_num, self.current_file)

    def declare(self, name: str, value: Any, line_num: int) -> None:
        for scope in reversed(self.scopes[self._scope_floor:]):
            if name in scope:
                scope[name] = value
                return
        self.scopes[-1][name] = value

    def assign(self, name: str, value: Any, line_num: int) -> None:
        for scope in reversed(self.scopes):
            if name in scope:
                scope[name] = value
                return
        raise TeaRuntimeError(f"Cannot assign to undefined variable '{name}'.{suggest(name, self._visible_names())}",
                              line_num, self.current_file)

    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        self.scopes.pop()

    def _check_size(self, value: Any, line_num: int | None) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            if value.bit_length() > MAX_INT_BITS:
                raise TeaRuntimeError("Integer result is too large", line_num, self.current_file)
        elif isinstance(value, str):
            if len(value) > MAX_STR_CHARS:
                raise TeaRuntimeError(f"String exceeds {MAX_STR_CHARS} characters", line_num, self.current_file)
        elif isinstance(value, list):
            if len(value) > MAX_LIST_ITEMS:
                raise TeaRuntimeError(f"Array exceeds {MAX_LIST_ITEMS} items", line_num, self.current_file)
        elif isinstance(value, dict):
            if len(value) > MAX_MAP_ITEMS:
                raise TeaRuntimeError(f"Map exceeds {MAX_MAP_ITEMS} entries", line_num, self.current_file)
        return value

    # ---------------- expression evaluation ----------------
    def eval_expr(self, node: ast.AST | None, line_num: int | None = None) -> Any:
        if node is None:
            return None
        self._tick(line_num)

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            if node.id == "true":
                return True
            if node.id == "false":
                return False
            if node.id == "null":
                return None
            return self.lookup(node.id, line_num or 0)

        if isinstance(node, (ast.List, ast.Tuple)):
            return self._check_size([self.eval_expr(e, line_num) for e in node.elts], line_num)

        if isinstance(node, ast.Dict):
            out: dict = {}
            for k, v in zip(node.keys, node.values):
                key = self.eval_expr(k, line_num)
                if isinstance(key, (list, dict)):
                    raise TeaRuntimeError("Map keys must be strings, numbers, booleans or null",
                                          line_num, self.current_file)
                out[key] = self.eval_expr(v, line_num)
            return self._check_size(out, line_num)

        if isinstance(node, ast.Slice):
            return slice(
                self.eval_expr(node.lower, line_num) if node.lower is not None else None,
                self.eval_expr(node.upper, line_num) if node.upper is not None else None,
                self.eval_expr(node.step, line_num) if node.step is not None else None,
            )

        if isinstance(node, ast.Subscript):
            container = self.eval_expr(node.value, line_num)
            key = self.eval_expr(node.slice, line_num)
            return self._do_index(container, key, line_num)

        if isinstance(node, ast.Call):
            args = [self.eval_expr(a, line_num) for a in node.args]
            if isinstance(node.func, ast.Name):
                return self.call_function(node.func.id, args, line_num or 0)
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                lib, func = node.func.value.id, node.func.attr
                if lib not in self.imported_libs or func not in LIBRARY_APIS.get(lib, set()):
                    raise TeaRuntimeError(f"Unknown or private function {lib}.{func}", line_num, self.current_file)
                try:
                    result = getattr(self.libraries[lib], func)(*args)
                except TeaError:
                    raise
                except Exception as exc:
                    raise TeaRuntimeError(f"{lib}.{func} failed: {exc}", line_num, self.current_file) from exc
                return self._check_size(result, line_num)
            raise TeaRuntimeError("Unsupported call target", line_num, self.current_file)

        if isinstance(node, ast.UnaryOp):
            value = self.eval_expr(node.operand, line_num)
            if isinstance(node.op, ast.Not):
                return not value
            if not is_number(value):
                raise TeaRuntimeError("Unary + and - require a number", line_num, self.current_file)
            return value if isinstance(node.op, ast.UAdd) else -value

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for vn in node.values:
                    result = self.eval_expr(vn, line_num)
                    if not result:
                        return result
                return result
            result = False
            for vn in node.values:
                result = self.eval_expr(vn, line_num)
                if result:
                    return result
            return result

        if isinstance(node, ast.BinOp):
            left = self.eval_expr(node.left, line_num)
            right = self.eval_expr(node.right, line_num)
            return self._binary_op(node.op, left, right, line_num)

        if isinstance(node, ast.Compare):
            left = self.eval_expr(node.left, line_num)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.eval_expr(comparator, line_num)
                if not self._compare(op, left, right, line_num):
                    return False
                left = right
            return True

        raise TeaRuntimeError(f"Unsupported expression node: {type(node).__name__}", line_num, self.current_file)

    def _do_index(self, container: Any, key: Any, line_num: int | None) -> Any:
        if isinstance(container, dict):
            if isinstance(key, slice):
                raise TeaRuntimeError("Maps cannot be sliced", line_num, self.current_file)
            if isinstance(key, (list, dict)):
                raise TeaRuntimeError("Map keys must be strings, numbers, booleans or null",
                                      line_num, self.current_file)
            if key not in container:
                raise TeaRuntimeError(f"Map has no key {tea_repr(key)!r}.{suggest(str(key), [str(k) for k in container])}",
                                      line_num, self.current_file)
            return container[key]

        if isinstance(container, (list, str)):
            if isinstance(key, slice):
                return container[key]
            if not isinstance(key, int) or isinstance(key, bool):
                raise TeaRuntimeError("Index must be an integer", line_num, self.current_file)
            idx = key + len(container) if key < 0 else key
            if not 0 <= idx < len(container):
                raise TeaRuntimeError(f"Index {key} out of range (length {len(container)})",
                                      line_num, self.current_file)
            return container[idx]

        kind = "null" if container is None else type(container).__name__
        raise TeaRuntimeError(f"Cannot index a {kind}; expected an array, string or map",
                              line_num, self.current_file)

    def _binary_op(self, op: ast.AST, left: Any, right: Any, line_num: int | None) -> Any:
        try:
            if isinstance(op, ast.Add):
                if isinstance(left, str) and isinstance(right, str):
                    return self._check_size(left + right, line_num)
                if isinstance(left, list) and isinstance(right, list):
                    return self._check_size(left + right, line_num)
                if isinstance(left, dict) and isinstance(right, dict):
                    return self._check_size({**left, **right}, line_num)
                if is_number(left) and is_number(right):
                    return self._check_size(left + right, line_num)
                raise TeaRuntimeError("'+' requires two numbers, two strings, two arrays or two maps",
                                      line_num, self.current_file)

            if isinstance(op, ast.Sub):
                if is_number(left) and is_number(right):
                    return self._check_size(left - right, line_num)
                raise TeaRuntimeError("'-' requires numbers", line_num, self.current_file)

            if isinstance(op, ast.Mult):
                if is_number(left) and is_number(right):
                    return self._check_size(left * right, line_num)
                if isinstance(right, (str, list)) and isinstance(left, int) and not isinstance(left, bool):
                    left, right = right, left
                if isinstance(left, (str, list)) and isinstance(right, int) and not isinstance(right, bool):
                    count = max(right, 0)
                    if count > MAX_REPEAT or len(left) * count > MAX_REPEAT:
                        raise TeaRuntimeError(f"Repetition exceeds limit ({MAX_REPEAT})", line_num, self.current_file)
                    if isinstance(left, str):
                        return self._check_size(left * count, line_num)
                    # Independent copies: [row] * 3 must not alias the same row.
                    return self._check_size([copy.deepcopy(item) for _ in range(count) for item in left], line_num)
                raise TeaRuntimeError("'*' requires numbers, or a string/array times an integer",
                                      line_num, self.current_file)

            if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if not (is_number(left) and is_number(right)):
                    raise TeaRuntimeError("Division and modulo require numbers", line_num, self.current_file)
                if right == 0:
                    raise TeaRuntimeError("Division by zero", line_num, self.current_file)
                if isinstance(op, ast.Div):
                    return left / right
                if isinstance(op, ast.FloorDiv):
                    return left // right
                return left % right

            if isinstance(op, ast.Pow):
                if is_number(left) and is_number(right):
                    if abs(right) > MAX_POW_EXP:
                        raise TeaRuntimeError(f"Exponent exceeds limit ({MAX_POW_EXP})", line_num, self.current_file)
                    if left < 0 and isinstance(right, float) and not right.is_integer():
                        raise TeaRuntimeError("Negative base with a fractional exponent is not supported",
                                              line_num, self.current_file)
                    if isinstance(left, int) and isinstance(right, int) and right > 0:
                        if max(left.bit_length(), 1) * right > MAX_INT_BITS:
                            raise TeaRuntimeError("Integer result is too large", line_num, self.current_file)
                    return self._check_size(left ** right, line_num)
                raise TeaRuntimeError("'**' requires numbers", line_num, self.current_file)

            raise TeaRuntimeError("Unsupported binary operator", line_num, self.current_file)
        except TeaError:
            raise
        except ZeroDivisionError as exc:
            raise TeaRuntimeError("Division by zero", line_num, self.current_file) from exc
        except OverflowError as exc:
            raise TeaRuntimeError("Numeric overflow", line_num, self.current_file) from exc
        except (TypeError, ValueError) as exc:
            raise TeaRuntimeError(f"Invalid operands: {exc}", line_num, self.current_file) from exc

    def _compare(self, op: ast.AST, left: Any, right: Any, line_num: int | None) -> bool:
        try:
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.NotEq):
                return left != right
            if isinstance(op, ast.In):
                if isinstance(right, (list, str, dict)):
                    return left in right
                raise TeaRuntimeError("'in' needs an array, string or map on the right", line_num, self.current_file)
            if isinstance(op, ast.NotIn):
                if isinstance(right, (list, str, dict)):
                    return left not in right
                raise TeaRuntimeError("'not in' needs an array, string or map on the right", line_num, self.current_file)
            if isinstance(op, ast.Lt):
                return left < right
            if isinstance(op, ast.LtE):
                return left <= right
            if isinstance(op, ast.Gt):
                return left > right
            if isinstance(op, ast.GtE):
                return left >= right
            raise TeaRuntimeError("Unsupported comparison", line_num, self.current_file)
        except TypeError as exc:
            raise TeaRuntimeError(f"Invalid comparison: {exc}", line_num, self.current_file) from exc

    # ---------------- functions / execution ----------------
    def call_function(self, name: str, args: list[Any], line_num: int) -> Any:
        func = self.functions.get(name)
        if func is None:
            raise TeaRuntimeError(f"Undefined function '{name}'.{suggest(name, self.functions)}",
                                  line_num, self.current_file)
        params, defaults = func["params"], func["defaults"]
        required = len(params) - len(defaults)
        if not required <= len(args) <= len(params):
            expected = str(required) if not defaults else f"{required} to {len(params)}"
            raise TeaRuntimeError(f"Function '{name}' expects {expected} args, got {len(args)}",
                                  line_num, self.current_file)
        if self.call_depth >= self.max_recursion:
            raise TeaRuntimeError(f"Maximum recursion depth exceeded ({self.max_recursion})",
                                  line_num, self.current_file)

        saved_scopes, saved_floor = self.scopes, self._scope_floor
        local: dict[str, Any] = {}
        self.scopes = [saved_scopes[0], local]
        self._scope_floor = 1
        self.call_depth += 1
        try:
            for i, p in enumerate(params):
                local[p] = args[i] if i < len(args) else self.eval_expr(defaults[i - required], line_num)
            self.run_block(func["body"])
            return None
        except _ReturnSignal as ret:
            return ret.value
        finally:
            self.call_depth -= 1
            self.scopes = saved_scopes
            self._scope_floor = saved_floor

    def _run_loop_body(self, body: list[tuple]) -> bool:
        """Run one iteration. Returns False when the loop should break."""
        self.push_scope()
        try:
            self.run_block(body)
        except _ContinueSignal:
            pass
        except _BreakSignal:
            return False
        finally:
            self.pop_scope()
        return True

    def execute(self, instr: tuple) -> None:
        op = instr[0]
        line = instr[-1]
        self._tick(line)

        if op == "PRINT":
            print(tea_repr(self.eval_expr(instr[1], line)))

        elif op == "STORE":
            self.declare(instr[1], self.eval_expr(instr[2], line), line)

        elif op == "ASSIGN":
            self.assign(instr[1], self.eval_expr(instr[2], line), line)

        elif op == "SET_INDEX":
            container = self.eval_expr(instr[1], line)
            key = self.eval_expr(instr[2], line)
            value = self.eval_expr(instr[3], line)
            self._set_index(container, key, value, line)

        elif op == "IF":
            cond = self.eval_expr(instr[1], line)
            self.push_scope()
            try:
                if cond:
                    self.run_block(instr[2])
                elif instr[3]:
                    self.run_block(instr[3])
            finally:
                self.pop_scope()

        elif op == "LOOPS":
            iterations = 0
            while True:
                self._tick(line)
                if not self.eval_expr(instr[1], line):
                    break
                iterations += 1
                if iterations > self.max_loop_iterations:
                    raise TeaRuntimeError(f"Loop iteration limit exceeded ({self.max_loop_iterations})",
                                          line, self.current_file)
                if not self._run_loop_body(instr[2]):
                    break

        elif op == "FOREACH":
            collection = self.eval_expr(instr[2], line)
            if isinstance(collection, list):
                items = list(collection)
            elif isinstance(collection, str):
                items = list(collection)
            elif isinstance(collection, dict):
                items = list(collection.keys())
            else:
                raise TeaRuntimeError("foreach needs an array, string or map", line, self.current_file)
            if len(items) > self.max_loop_iterations:
                raise TeaRuntimeError(f"Loop iteration limit exceeded ({self.max_loop_iterations})",
                                      line, self.current_file)
            for item in items:
                self._tick(line)
                self.push_scope()
                try:
                    self.scopes[-1][instr[1]] = item
                    try:
                        self.run_block(instr[3])
                    except _ContinueSignal:
                        continue
                    except _BreakSignal:
                        break
                finally:
                    self.pop_scope()

        elif op == "CALL_EXPR":
            self.eval_expr(instr[1], line)

        elif op == "RETURN":
            raise _ReturnSignal(self.eval_expr(instr[1], line))

        elif op == "BREAK":
            raise _BreakSignal()

        elif op == "CONTINUE":
            raise _ContinueSignal()

    def _set_index(self, container: Any, key: Any, value: Any, line: int) -> None:
        if isinstance(container, dict):
            if isinstance(key, (list, dict)):
                raise TeaRuntimeError("Map keys must be strings, numbers, booleans or null", line, self.current_file)
            container[key] = value
            self._check_size(container, line)
            return
        if isinstance(container, list):
            if not isinstance(key, int) or isinstance(key, bool):
                raise TeaRuntimeError("Array index must be an integer", line, self.current_file)
            idx = key + len(container) if key < 0 else key
            if not 0 <= idx < len(container):
                raise TeaRuntimeError(f"Index {key} out of range (length {len(container)}); "
                                      "use arrayl.push to grow an array", line, self.current_file)
            container[idx] = value
            return
        if isinstance(container, str):
            raise TeaRuntimeError("Strings are immutable; build a new string instead", line, self.current_file)
        raise TeaRuntimeError("Indexed assignment needs an array or a map", line, self.current_file)

    def run_block(self, block: list[tuple]) -> None:
        for instr in block:
            if instr[0] not in ("BOOTSTRAP", "IMPORT", "FUNC_DEF"):
                self.execute(instr)

    def run_vm(self, bytecode: list[tuple]) -> int:
        self.scopes = [{}]
        self.steps = 0
        self.call_depth = 0
        self._scope_floor = 0
        if not self.quiet:
            print("\n--- Tea VM ---")
        try:
            self.run_block(bytecode)
        except TeaError as exc:
            print(format_error(exc, self.source_lines))
            return 1
        except RecursionError:
            print(format_error(TeaRuntimeError("Expression or call nesting is too deep", None, self.current_file),
                               self.source_lines))
            return 1
        except EOFError as exc:
            print(format_error(TeaRuntimeError(f"Input ended unexpectedly: {exc}", None, self.current_file),
                               self.source_lines))
            return 1
        except KeyboardInterrupt:
            print("\n[Tea] Interrupted.")
            return 130
        finally:
            GraphicLibrary._on_close()
            if GameLibrary._running:
                GameLibrary.quit()
        if not self.quiet:
            print("--- Done ---")
        return 0

# ==========================================
# Embedded programs
# ==========================================
DEMO_SOURCE = '''// Tea demo
import.tea.util;
<import mathl>;

function fib(n) {
    if (n <= 1) { return n; }
    return fib(n - 1) + fib(n - 2);
}

variable i = 0;
loops (i < 8) { iol.print(fib(i)); i = i + 1; }

variable msg = "Hello, world";
iol.print(msg);
iol.print(mathl.sqrt(16) + 1);
iol.print([[1, 2], [3, 4]][1][0]);
'''

TOUR_SOURCE = '''// ---- Tea feature tour ----
import.tea.util;
<import iol>; <import stringl>; <import arrayl>; <import typel>; <import mathl>;

// several statements on one line, and a statement across lines
variable a = 1; variable b = 2;
variable total = a +
                 b;
iol.print(total);

// arrays: indexed assignment, growth, slicing
variable xs = [5, 3, 9, 1];
xs[0] = 50;
arrayl.push(xs, 7);
iol.print(xs);
iol.print(xs[1:3]);
iol.print(xs[::-1]);
iol.print(arrayl.sorted(xs));
iol.print(arrayl.sum(xs));

// repetition makes independent copies (no aliasing)
variable grid = [[0, 0]] * 3;
grid[0][0] = 99;
iol.print(grid);

// maps
variable user = {"name": "Ada", "age": 36};
user["age"] = 37;
user["lang"] = "Tea";
iol.print(user["name"]); iol.print(typel.len(user));
iol.print(typel.keys(user));

// foreach over an array, a map and a string
variable sum = 0;
foreach (n in [1, 2, 3, 4]) { sum = sum + n; }
iol.print(sum);
foreach (k in user) { iol.print(k); }
foreach (ch in "tea") { iol.write(ch); }
iol.print("");

// default arguments + recursion
function greet(name, greeting = "Hello") { return greeting + ", " + name + "!"; }
iol.print(greet("World"));
iol.print(greet("Tea", "Hi"));

// strings
variable s = "  Hello Tea World  ";
iol.print(stringl.strip(s));
iol.print(stringl.split(stringl.strip(s), " "));
iol.print(stringl.join(["a", "b", "c"], "-"));
iol.print(stringl.upper(stringl.replace(s, "World", "Language")));

// membership, types, math
iol.print(3 in [1, 2, 3]);
iol.print("tea" in "steamy");
iol.print(typel.type_of(user));
iol.print(mathl.clamp(42, 0, 10));
iol.print(mathl.round(mathl.pi(), 3));

// loops with break / continue
variable kept = [];
foreach (n in arrayl.range(1, 11)) {
    if (n % 2 == 0) { continue; }
    if (n > 7) { break; }
    arrayl.push(kept, n);
}
iol.print(kept);
'''


# ==========================================
# Regression tests  (python tea.py --self-test)
# ==========================================
def _run_self_tests(verbosity: int = 2) -> int:
    import contextlib
    import io
    import unittest

    def run_source(source: str, *, run: bool = True, **limits):
        compiler = TeaCompiler(**limits)
        compiler.quiet = True
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bc = compiler.compile_string(source, name="test.tea")
            code = compiler.run_vm(bc) if (run and bc is not None) else None
        return compiler, bc, code, out.getvalue()

    class TeaTests(unittest.TestCase):
        def assert_ok(self, source, expected_lines=None, **limits):
            compiler, bc, code, out = run_source(source, **limits)
            self.assertIsNotNone(bc, out)
            self.assertEqual(code, 0, out)
            if expected_lines is not None:
                lines = [line for line in out.splitlines() if line]
                self.assertEqual(lines, expected_lines, out)
            return out

        def assert_compile_fail(self, source, contains=None):
            compiler, bc, code, out = run_source(source, run=False)
            self.assertIsNone(bc, out)
            self.assertTrue(compiler.errors)
            text = out + "\n".join(str(e) for e in compiler.errors)
            if contains:
                self.assertIn(contains, text)
            return text

        def assert_runtime_fail(self, source, contains=None, **limits):
            compiler, bc, code, out = run_source(source, **limits)
            self.assertIsNotNone(bc, out)
            self.assertEqual(code, 1, out)
            if contains:
                self.assertIn(contains, out)
            return out

        # ---- core / regressions ----
        def test_bootstrap_after_comments(self):
            self.assert_ok("// header\n\nimport.tea.util;\niol.print(\"ok\");\n", ["ok"])

        def test_function_and_return_inside_if(self):
            self.assert_ok("""import.tea.util;
function clamp(x) { if (x < 0) { return 0; } return x; }
iol.print(clamp(-5)); iol.print(clamp(7));
""", ["0", "7"])

        def test_loop_counter_and_assignment(self):
            self.assert_ok("""import.tea.util;
variable i = 0; variable sum = 0;
loops (i < 5) { i = i + 1; sum = sum + i; }
iol.print(sum);
""", ["15"], max_loop_iterations=20)

        def test_variable_rebinding_in_loop(self):
            self.assert_ok("""import.tea.util;
variable i = 0;
loops (i < 3) { variable i = i + 1; }
iol.print(i);
""", ["3"], max_loop_iterations=20)

        def test_comma_string_and_nested_calls(self):
            self.assert_ok("""import.tea.util;
<import iol>; <import mathl>;
iol.print_msg("Hello, world");
iol.print(mathl.sqrt(mathl.power(3, 2)));
""", ["[IOL] Hello, world", "3.0"])

        def test_nested_arrays_and_escapes(self):
            self.assert_ok('import.tea.util;\nvariable a = [[1, 2], [3, 4]];\n'
                           'iol.print(a[1][0]);\niol.print("a\\nb");\niol.print("x" + "y");\n',
                           ["3", "a", "b", "xy"])

        def test_literals(self):
            self.assert_ok("import.tea.util;\niol.print(true);\niol.print(false);\niol.print(null);\n",
                           ["true", "false", "null"])

        def test_mixed_library_expression(self):
            self.assert_ok("import.tea.util;\n<import mathl>;\niol.print(mathl.sqrt(16) + 1);\n", ["5.0"])

        def test_unknown_library_import(self):
            self.assert_compile_fail("import.tea.util;\n<import nosuchl>;\n", "Unknown library")

        def test_library_typo_suggestion(self):
            self.assert_compile_fail("import.tea.util;\n<import mathl>;\niol.print(mathl.sqt(4));\n",
                                     "Did you mean 'sqrt'")

        def test_private_library_method_rejected(self):
            self.assert_compile_fail("import.tea.util;\n<import mathl>;\niol.print(mathl.__init__());\n",
                                     "Unknown or private function")

        def test_python_eval_escape_rejected(self):
            self.assert_compile_fail("import.tea.util;\niol.print((1).__class__.__mro__);\n", "Unsupported expression")

        def test_python_builtin_call_rejected(self):
            self.assert_compile_fail("import.tea.util;\niol.print(__import__('os'));\n", "Invalid function name")

        def test_unclosed_brace(self):
            self.assert_compile_fail("import.tea.util;\nif (true) {\n    iol.print(1);\n", "Unclosed block")

        def test_stray_brace(self):
            self.assert_compile_fail("import.tea.util;\n}\n", "Unexpected '}'")

        def test_else_without_if(self):
            self.assert_compile_fail("import.tea.util;\nelse {\n}\n", "without a matching 'if'")

        def test_else_block(self):
            self.assert_ok('import.tea.util;\nif (false) { iol.print("no"); } else { iol.print("yes"); }\n', ["yes"])

        def test_else_if_chain(self):
            self.assert_ok("""import.tea.util;
function grade(x) {
    if (x >= 90) { return "A"; } else if (x >= 80) { return "B"; } else { return "C"; }
}
iol.print(grade(95)); iol.print(grade(85)); iol.print(grade(70));
""", ["A", "B", "C"])

        def test_recursion_limit(self):
            self.assert_runtime_fail("import.tea.util;\nfunction f(n) { return f(n + 1); }\niol.print(f(0));\n",
                                     "Maximum recursion depth", max_recursion=10)

        def test_loop_limit(self):
            self.assert_runtime_fail("import.tea.util;\nloops (true) {\n}\n",
                                     "Loop iteration limit", max_loop_iterations=5, max_steps=1000)

        def test_step_limit(self):
            self.assert_runtime_fail("""import.tea.util;
variable i = 0;
loops (i < 1000000) { i = i + 1; }
""", "Execution step limit", max_steps=50, max_loop_iterations=1000000)

        def test_function_arity(self):
            self.assert_runtime_fail("import.tea.util;\nfunction f(a, b) { return a; }\niol.print(f(1));\n",
                                     "expects 2 args")

        def test_duplicate_params(self):
            self.assert_compile_fail("import.tea.util;\nfunction f(a, a) { return a; }\n", "Duplicate parameter")

        def test_duplicate_function(self):
            self.assert_compile_fail("import.tea.util;\nfunction f() { return 1; }\nfunction f() { return 2; }\n",
                                     "Duplicate function")

        def test_undefined_function_suggestion(self):
            self.assert_runtime_fail("import.tea.util;\nfunction total() { return 1; }\niol.print(totl());\n",
                                     "Did you mean 'total'")

        def test_undefined_variable_suggestion(self):
            self.assert_runtime_fail("import.tea.util;\nvariable counter = 1;\niol.print(countr);\n",
                                     "Did you mean 'counter'")

        def test_no_dynamic_scope(self):
            self.assert_runtime_fail("""import.tea.util;
function peek() { return secret; }
function main() { variable secret = 42; return peek(); }
iol.print(main());
""", "Undefined variable 'secret'")

        def test_global_read_from_function(self):
            self.assert_ok("import.tea.util;\nvariable g = 10;\nfunction f(x) { return x + g; }\niol.print(f(5));\n",
                           ["15"])

        def test_break_continue(self):
            self.assert_ok("""import.tea.util;
variable i = 0; variable sum = 0;
loops (i < 10) {
    i = i + 1;
    if (i == 3) { continue; }
    if (i == 6) { break; }
    sum = sum + i;
}
iol.print(sum);
""", ["12"], max_loop_iterations=20)

        def test_return_outside_function(self):
            self.assert_compile_fail("import.tea.util;\nreturn 1;\n", "only allowed inside a function")

        def test_break_outside_loop(self):
            self.assert_compile_fail("import.tea.util;\nbreak;\n", "only allowed inside a loop")

        def test_import_inside_block(self):
            self.assert_compile_fail("import.tea.util;\nif (true) {\n<import mathl>;\n}\n",
                                     "Imports must be at top level")

        def test_division_by_zero(self):
            self.assert_runtime_fail("import.tea.util;\niol.print(1 / 0);\n", "Division by zero")

        def test_library_statement_parses(self):
            compiler, bc, code, out = run_source(
                "import.tea.util;\n<import graphicl>;\ngraphicl.window(300, 300);\n", run=False)
            self.assertIsNotNone(bc, out)
            self.assertEqual(bc[-1][0], "CALL_EXPR")

        def test_boolean_short_circuit(self):
            self.assert_ok("""import.tea.util;
function boom() { return 1 / 0; }
iol.print(false and boom());
iol.print(true or boom());
""", ["false", "true"])

        def test_missing_bootstrap(self):
            self.assert_compile_fail("iol.print(1);\n", "Invalid bootstrap")

        def test_duplicate_bootstrap(self):
            self.assert_compile_fail("import.tea.util;\nimport.tea.util;\n", "only allowed as the first")

        def test_forward_import_for_function_body(self):
            self.assert_ok("""import.tea.util;
function f(x) { return mathl.sqrt(x); }
<import mathl>;
iol.print(f(9));
""", ["3.0"])

        def test_negative_fractional_power_rejected(self):
            self.assert_runtime_fail("import.tea.util;\niol.print((-1) ** 0.5);\n", "Negative base")

        def test_embedded_demo_runs(self):
            self.assert_ok(DEMO_SOURCE, ["0", "1", "1", "2", "3", "5", "8", "13", "Hello, world", "5.0", "3"])

        def test_comment_marker_inside_string(self):
            self.assert_ok('import.tea.util;\niol.print("http://example.com");\n', ["http://example.com"])

        def test_inline_comment(self):
            self.assert_ok("import.tea.util;\niol.print(1); // trailing comment\n", ["1"])

        def test_loop_nesting_depth_guard(self):
            n = MAX_LOOP_DEPTH + 1
            self.assert_compile_fail("import.tea.util;\n" + "loops (true) {\n"*n + "}\n"*n,
                                     "Maximum loop nesting depth")

        def test_deep_if_nesting_is_clean_error(self):
            self.assert_compile_fail("import.tea.util;\n" + "if (true) {\n"*1200 + "}\n"*1200, "nesting depth")

        def test_long_else_if_chain_is_clean_error(self):
            self.assert_compile_fail("import.tea.util;\n" + "if (false) {\n} else if (false) {\n"*1200
                                     + "} else {\n}\n", "nesting depth")

        def test_deep_parentheses_error_is_truncated(self):
            out = self.assert_compile_fail("import.tea.util;\niol.print(" + "("*3000 + "1" + ")"*3000 + ");\n")
            self.assertLess(len(out), 2000)

        def test_repeated_squaring_is_capped(self):
            self.assert_runtime_fail("""import.tea.util;
variable x = 2; variable i = 0;
loops (i < 40) { x = x * x; i = i + 1; }
""", "too large", max_loop_iterations=100)

        def test_repeated_string_concat_is_capped(self):
            self.assert_runtime_fail("""import.tea.util;
variable s = "ab"; variable i = 0;
loops (i < 40) { s = s + s; i = i + 1; }
""", "characters", max_loop_iterations=100)

        def test_variable_in_function_shadows_global(self):
            self.assert_ok("""import.tea.util;
variable counter = 100;
function reset() { variable counter = 0; return counter; }
iol.print(reset()); iol.print(counter);
""", ["0", "100"])

        def test_assignment_in_function_can_update_global(self):
            self.assert_ok("""import.tea.util;
variable total = 0;
function bump() { total = total + 5; return total; }
iol.print(bump()); iol.print(total);
""", ["5", "5"])

        def test_function_local_loop_counter(self):
            self.assert_ok("""import.tea.util;
function count(n) {
    variable i = 0; variable s = 0;
    loops (i < n) { variable i = i + 1; s = s + i; }
    return s;
}
iol.print(count(5));
""", ["15"], max_loop_iterations=50)

        # ---- NEW: layout ----
        def test_multiple_statements_per_line(self):
            self.assert_ok("import.tea.util;\niol.print(1); iol.print(2); iol.print(3);\n", ["1", "2", "3"])

        def test_statement_spans_lines(self):
            self.assert_ok("import.tea.util;\nvariable x = 1 +\n 2 +\n 3;\niol.print(x);\n", ["6"])

        def test_whole_block_on_one_line(self):
            self.assert_ok("import.tea.util;\nfunction f(n) { return n * 2; }\niol.print(f(21));\n", ["42"])

        def test_if_else_all_on_one_line(self):
            self.assert_ok('import.tea.util;\nif (false) { iol.print("a"); } else { iol.print("b"); }\n', ["b"])

        def test_semicolon_inside_string(self):
            self.assert_ok('import.tea.util;\niol.print("a;b;c");\n', ["a;b;c"])

        def test_brace_inside_string_does_not_open_block(self):
            self.assert_ok('import.tea.util;\niol.print("{ not a block }");\n', ["{ not a block }"])

        # ---- NEW: indexed assignment ----
        def test_array_index_assignment(self):
            self.assert_ok("import.tea.util;\nvariable a = [1, 2, 3];\na[1] = 9;\niol.print(a);\n", ["[1, 9, 3]"])

        def test_nested_array_index_assignment(self):
            self.assert_ok("""import.tea.util;
variable g = [[1, 2], [3, 4]];
g[1][0] = 99;
iol.print(g);
""", ["[[1, 2], [99, 4]]"])

        def test_negative_index_assignment(self):
            self.assert_ok("import.tea.util;\nvariable a = [1, 2, 3];\na[-1] = 7;\niol.print(a);\n", ["[1, 2, 7]"])

        def test_index_assignment_out_of_range(self):
            self.assert_runtime_fail("import.tea.util;\nvariable a = [1];\na[5] = 2;\n", "out of range")

        def test_string_is_immutable(self):
            self.assert_runtime_fail('import.tea.util;\nvariable s = "abc";\ns[0] = "z";\n', "immutable")

        def test_computed_index_assignment(self):
            self.assert_ok("""import.tea.util;
variable a = [0, 0, 0]; variable i = 1;
a[i + 1] = i * 5;
iol.print(a);
""", ["[0, 0, 5]"])

        # ---- NEW: no aliasing on repetition ----
        def test_list_repetition_makes_independent_copies(self):
            self.assert_ok("""import.tea.util;
variable grid = [[0, 0]] * 3;
grid[0][0] = 99;
iol.print(grid);
""", ["[[99, 0], [0, 0], [0, 0]]"])

        def test_arrayl_deep_copy(self):
            self.assert_ok("""import.tea.util;
<import arrayl>;
variable a = [[1]]; variable b = arrayl.deep_copy(a);
b[0][0] = 5;
iol.print(a); iol.print(b);
""", ["[[1]]", "[[5]]"])

        # ---- NEW: maps ----
        def test_map_literal_and_access(self):
            self.assert_ok('import.tea.util;\nvariable m = {"a": 1, "b": 2};\niol.print(m["b"]);\n', ["2"])

        def test_map_assignment_and_insert(self):
            self.assert_ok("""import.tea.util;
variable m = {"a": 1};
m["a"] = 5; m["b"] = 2;
iol.print(m["a"]); iol.print(m["b"]);
""", ["5", "2"])

        def test_map_missing_key(self):
            self.assert_runtime_fail('import.tea.util;\nvariable m = {"a": 1};\niol.print(m["z"]);\n',
                                     "Map has no key")

        def test_map_nested(self):
            self.assert_ok("""import.tea.util;
variable cfg = {"db": {"host": "localhost", "port": 5432}};
cfg["db"]["port"] = 5433;
iol.print(cfg["db"]["port"]);
""", ["5433"])

        def test_map_in_expression_without_block_confusion(self):
            self.assert_ok("""import.tea.util;
<import typel>;
variable m = {"k": 1};
if (typel.is_map(m)) { iol.print("map"); }
""", ["map"])

        def test_map_keys_and_values(self):
            self.assert_ok("""import.tea.util;
<import typel>;
variable m = {"a": 1, "b": 2};
iol.print(typel.keys(m)); iol.print(typel.values(m)); iol.print(typel.has(m, "a"));
""", ['["a", "b"]', "[1, 2]", "true"])

        def test_map_membership(self):
            self.assert_ok('import.tea.util;\nvariable m = {"a": 1};\niol.print("a" in m);\niol.print("z" in m);\n',
                           ["true", "false"])

        def test_map_rejects_unhashable_key(self):
            self.assert_runtime_fail("import.tea.util;\nvariable m = {[1, 2]: 3};\n", "Map keys must be")

        # ---- NEW: foreach ----
        def test_foreach_array(self):
            self.assert_ok("""import.tea.util;
variable s = 0;
foreach (n in [1, 2, 3, 4]) { s = s + n; }
iol.print(s);
""", ["10"])

        def test_foreach_string(self):
            self.assert_ok('import.tea.util;\nforeach (c in "abc") { iol.print(c); }\n', ["a", "b", "c"])

        def test_foreach_map_keys(self):
            self.assert_ok('import.tea.util;\nforeach (k in {"x": 1, "y": 2}) { iol.print(k); }\n', ["x", "y"])

        def test_foreach_break_continue(self):
            self.assert_ok("""import.tea.util;
<import arrayl>;
variable kept = [];
foreach (n in arrayl.range(1, 11)) {
    if (n % 2 == 0) { continue; }
    if (n > 7) { break; }
    arrayl.push(kept, n);
}
iol.print(kept);
""", ["[1, 3, 5, 7]"])

        def test_foreach_nested(self):
            self.assert_ok("""import.tea.util;
variable pairs = 0;
foreach (a in [1, 2]) { foreach (b in [1, 2, 3]) { pairs = pairs + 1; } }
iol.print(pairs);
""", ["6"])

        def test_foreach_needs_collection(self):
            self.assert_runtime_fail("import.tea.util;\nforeach (x in 5) { iol.print(x); }\n",
                                     "foreach needs an array")

        def test_foreach_loop_variable_is_scoped(self):
            self.assert_runtime_fail("import.tea.util;\nforeach (n in [1]) { }\niol.print(n);\n",
                                     "Undefined variable 'n'")

        # ---- NEW: slicing / strings / in ----
        def test_array_slicing(self):
            self.assert_ok("""import.tea.util;
variable a = [1, 2, 3, 4, 5];
iol.print(a[1:3]); iol.print(a[:2]); iol.print(a[3:]); iol.print(a[::-1]);
""", ["[2, 3]", "[1, 2]", "[4, 5]", "[5, 4, 3, 2, 1]"])

        def test_string_indexing_and_slicing(self):
            self.assert_ok('import.tea.util;\nvariable s = "Tea";\niol.print(s[0]);\niol.print(s[-1]);\n'
                           'iol.print(s[0:2]);\n', ["T", "a", "Te"])

        def test_in_operator(self):
            self.assert_ok('import.tea.util;\niol.print(2 in [1, 2]);\niol.print("tea" in "steamy");\n'
                           'iol.print(9 not in [1, 2]);\n', ["true", "true", "true"])

        def test_index_out_of_range_message(self):
            self.assert_runtime_fail("import.tea.util;\nvariable a = [1];\niol.print(a[3]);\n",
                                     "out of range (length 1)")

        # ---- NEW: default arguments ----
        def test_default_arguments(self):
            self.assert_ok("""import.tea.util;
function greet(name, greeting = "Hello") { return greeting + ", " + name; }
iol.print(greet("A")); iol.print(greet("B", "Hi"));
""", ["Hello, A", "Hi, B"])

        def test_default_can_reference_earlier_param(self):
            self.assert_ok("import.tea.util;\nfunction f(a, b = a * 2) { return b; }\niol.print(f(5));\n", ["10"])

        def test_too_many_args_with_defaults(self):
            self.assert_runtime_fail("import.tea.util;\nfunction f(a, b = 1) { return a; }\niol.print(f(1, 2, 3));\n",
                                     "expects 1 to 2 args")

        def test_bare_return_gives_null(self):
            self.assert_ok("import.tea.util;\nfunction f() { return; }\niol.print(f());\n", ["null"])

        # ---- NEW: libraries ----
        def test_stringl(self):
            self.assert_ok("""import.tea.util;
<import stringl>;
iol.print(stringl.upper("tea"));
iol.print(stringl.split("a,b,c", ","));
iol.print(stringl.join(["x", "y"], "-"));
iol.print(stringl.replace("a-b", "-", "+"));
iol.print(stringl.strip("  hi  "));
iol.print(stringl.find("hello", "ll"));
iol.print(stringl.pad_left("7", 3, "0"));
iol.print(stringl.to_number("42") + 1);
""", ["TEA", '["a", "b", "c"]', "x-y", "a+b", "hi", "2", "007", "43"])

        def test_arrayl(self):
            self.assert_ok("""import.tea.util;
<import arrayl>;
variable a = [3, 1, 2];
arrayl.push(a, 4);
iol.print(a);
iol.print(arrayl.sorted(a));
iol.print(arrayl.sum(a));
iol.print(arrayl.pop(a));
iol.print(arrayl.range(3));
iol.print(arrayl.range(1, 8, 2));
iol.print(arrayl.unique([1, 1, 2]));
iol.print(arrayl.index_of(a, 2));
""", ["[3, 1, 2, 4]", "[1, 2, 3, 4]", "10", "4", "[0, 1, 2]", "[1, 3, 5, 7]", "[1, 2]", "2"])

        def test_arrayl_mutating_library_push(self):
            self.assert_ok("""import.tea.util;
<import arrayl>;
variable a = [];
foreach (n in [1, 2, 3]) { arrayl.push(a, n * n); }
iol.print(a);
""", ["[1, 4, 9]"])

        def test_typel(self):
            self.assert_ok("""import.tea.util;
<import typel>;
iol.print(typel.type_of(1));
iol.print(typel.type_of(1.5));
iol.print(typel.type_of("s"));
iol.print(typel.type_of([1]));
iol.print(typel.type_of({"a": 1}));
iol.print(typel.type_of(true));
iol.print(typel.type_of(null));
iol.print(typel.len("abcd"));
""", ["integer", "float", "string", "array", "map", "boolean", "null", "4"])

        def test_mathl_extras(self):
            self.assert_ok("""import.tea.util;
<import mathl>;
iol.print(mathl.floor(2.7));
iol.print(mathl.ceil(2.1));
iol.print(mathl.round(3.14159, 2));
iol.print(mathl.clamp(50, 0, 10));
iol.print(mathl.gcd(12, 18));
iol.print(mathl.min([4, 2, 9]));
iol.print(mathl.is_even(4));
""", ["2", "3", "3.14", "10", "6", "2", "true"])

        def test_timel_ticks(self):
            self.assert_ok("""import.tea.util;
<import timel>;
variable t = timel.ticks_ms();
iol.print(t > 0);
""", ["true"])

        def test_sleep_is_capped(self):
            self.assert_runtime_fail("import.tea.util;\n<import timel>;\ntimel.sleep(60);\n", "capped")

        def test_aill_false_string(self):
            self.assert_ok('import.tea.util;\n<import aill>;\niol.print(aill.evaluate_condition("false"));\n',
                           ["false"])

        def test_library_errors_are_tea_errors(self):
            self.assert_runtime_fail("import.tea.util;\n<import arrayl>;\niol.print(arrayl.pop([]));\n",
                                     "empty array")

        def test_all_libraries_importable(self):
            src = "import.tea.util;\n" + "".join(
                f"<import {name}>;\n" for name in LIBRARIES if name != "graphicl" and name != "gamel")
            src += "iol.print(1);\n"
            self.assert_ok(src, ["1"])

        def test_api_whitelist_has_no_privates(self):
            for lib, names in LIBRARY_APIS.items():
                for name in names:
                    self.assertFalse(name.startswith("_"), f"{lib}.{name} leaks a private name")
            self.assertNotIn("__class__", LIBRARY_APIS["mathl"])
            self.assertNotIn("_load_tk", LIBRARY_APIS["graphicl"])

        # ---- NEW: errors and tooling ----
        def test_error_shows_source_line(self):
            out = self.assert_runtime_fail("import.tea.util;\nvariable a = 1;\niol.print(a / 0);\n", "Division by zero")
            self.assertIn("| iol.print(a / 0);", out)

        def test_map_merge_operator(self):
            self.assert_ok('import.tea.util;\n<import typel>;\nvariable m = {"a": 1} + {"b": 2};\n'
                           'iol.print(typel.len(m));\n', ["2"])

        def test_feature_tour_runs(self):
            compiler, bc, code, out = run_source(TOUR_SOURCE)
            self.assertIsNotNone(bc, out)
            self.assertEqual(code, 0, out)
            self.assertIn("[[99, 0], [0, 0], [0, 0]]", out)
            self.assertIn("Hello, World!", out)
            self.assertIn("[1, 3, 5, 7]", out)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TeaTests)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


# ==========================================
# REPL
# ==========================================
def _repl_complete(text: str) -> bool:
    depth = 0
    quote = None
    escaped = False
    for line in text.splitlines():
        for ch in strip_comment(line):
            if quote is not None:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
    if depth > 0 or quote is not None:
        return False
    stripped = text.strip()
    return stripped.endswith(";") or stripped.endswith("}")


def run_repl(max_steps: int, max_recursion: int, max_loop_iterations: int) -> int:
    print("Tea interactive shell.  ':help' for help, ':quit' to exit.")
    globals_scope: dict[str, Any] = {}
    session_funcs: dict[str, Any] = {}
    session_imports: set[str] = set()
    buffer: list[str] = []

    while True:
        try:
            line = input("...> " if buffer else "tea> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not buffer:
            cmd = line.strip()
            if cmd in (":quit", ":q", "exit", "quit"):
                break
            if cmd in (":help", ":h"):
                print("  :vars   show variables      :funcs  show functions")
                print("  :libs   show libraries      :quit   exit")
                print("  End a statement with ';' and open blocks with '{'.")
                continue
            if cmd == ":vars":
                if not globals_scope:
                    print("  (none)")
                for k, v in globals_scope.items():
                    print(f"  {k} = {tea_repr(v)}")
                continue
            if cmd == ":funcs":
                print(", ".join(sorted(session_funcs)) or "(none)")
                continue
            if cmd == ":libs":
                print(", ".join(sorted(LIBRARIES)))
                continue
            if not cmd:
                continue
        buffer.append(line)
        text = "\n".join(buffer)
        if not _repl_complete(text):
            continue
        buffer = []

        prefix = BOOTSTRAP + "\n" + "".join(f"<import {lib}>;\n" for lib in sorted(session_imports))
        offset = prefix.count("\n")
        source = prefix + text + "\n"
        compiler = TeaCompiler(max_steps=max_steps, max_recursion=max_recursion,
                               max_loop_iterations=max_loop_iterations, allow_redefine=True)
        compiler.quiet = True
        compiler.echo_expressions = True
        try:
            bc = compiler.compile_string(source, name="<repl>")
            if bc is None:
                for err in compiler.errors:
                    ln = max(1, err.line_num - offset) if err.line_num else None
                    print(format_error(type(err)(err.message, ln, "<repl>"),
                                       text.splitlines(keepends=True)))
                continue
            session_imports |= compiler.imported_libs
            session_funcs.update(compiler.functions)
            compiler.functions = session_funcs
            compiler.scopes = [globals_scope]
            # Echo the value of a trailing bare call, like a normal shell.
            if bc and bc[-1][0] == "CALL_EXPR":
                call = bc[-1][1]
                target = call.func
                name = (target.value.id + "." + target.attr) if isinstance(target, ast.Attribute) \
                    else getattr(target, "id", "")
                if name not in ("iol.print", "iol.print_msg", "iol.write"):
                    bc[-1] = ("PRINT", call, bc[-1][-1])
            compiler.run_block(bc)
        except TeaError as exc:
            ln = max(1, exc.line_num - offset) if exc.line_num else None
            print(format_error(type(exc)(exc.message, ln, "<repl>"),
                               text.splitlines(keepends=True)))
        except (_ReturnSignal, _BreakSignal, _ContinueSignal):
            print("  -> return/break/continue are only valid inside a function or loop")
        except RecursionError:
            print("  -> nesting too deep")
        except KeyboardInterrupt:
            print("\n  -> interrupted")
        except Exception as exc:
            print(f"  -> {type(exc).__name__}: {exc}")
    print("Bye.")
    return 0


# ==========================================
# Bytecode dump
# ==========================================
def dump_bytecode(block: list[tuple], indent: int = 0) -> None:
    pad = "  " * indent
    for instr in block:
        op, line = instr[0], instr[-1]
        if op in ("PRINT", "CALL_EXPR", "RETURN"):
            expr = instr[1]
            detail = ast.unparse(expr) if isinstance(expr, ast.AST) else ""
            print(f"{pad}{line:>4}  {op:<10} {detail}")
        elif op in ("STORE", "ASSIGN"):
            print(f"{pad}{line:>4}  {op:<10} {instr[1]} = {ast.unparse(instr[2])}")
        elif op == "SET_INDEX":
            print(f"{pad}{line:>4}  {op:<10} {ast.unparse(instr[1])}[{ast.unparse(instr[2])}] = {ast.unparse(instr[3])}")
        elif op == "IF":
            print(f"{pad}{line:>4}  IF         {ast.unparse(instr[1])}")
            dump_bytecode(instr[2], indent + 1)
            if instr[3]:
                print(f"{pad}      ELSE")
                dump_bytecode(instr[3], indent + 1)
        elif op == "LOOPS":
            print(f"{pad}{line:>4}  LOOPS      {ast.unparse(instr[1])}")
            dump_bytecode(instr[2], indent + 1)
        elif op == "FOREACH":
            print(f"{pad}{line:>4}  FOREACH    {instr[1]} in {ast.unparse(instr[2])}")
            dump_bytecode(instr[3], indent + 1)
        else:
            print(f"{pad}{line:>4}  {op:<10} {' '.join(str(x) for x in instr[1:-1])}")


# ==========================================
# CLI
# ==========================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tea compiler + VM (single file: interpreter, libraries, demos, tests, REPL)",
        epilog="examples: python tea.py app.tea | --demo | --tour | --self-test | --repl",
    )
    parser.add_argument("source", nargs="?", default=None, help="Tea source file (default: app.tea)")
    parser.add_argument("--demo", action="store_true", help="run the built-in demo")
    parser.add_argument("--tour", action="store_true", help="run the feature tour")
    parser.add_argument("--self-test", action="store_true", help="run the regression suite")
    parser.add_argument("--repl", action="store_true", help="start an interactive shell")
    parser.add_argument("--check", action="store_true", help="compile only, do not run")
    parser.add_argument("--dump", action="store_true", help="print the compiled program")
    parser.add_argument("--max-steps", type=int, default=5_000_000)
    parser.add_argument("--max-recursion", type=int, default=200)
    parser.add_argument("--max-loop-iterations", type=int, default=1_000_000)
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_tests()
    if args.repl:
        return run_repl(args.max_steps, args.max_recursion, args.max_loop_iterations)

    compiler = TeaCompiler(max_steps=args.max_steps, max_recursion=args.max_recursion,
                           max_loop_iterations=args.max_loop_iterations)
    try:
        if args.demo:
            bytecode = compiler.compile_string(DEMO_SOURCE, name="demo.tea")
        elif args.tour:
            bytecode = compiler.compile_string(TOUR_SOURCE, name="tour.tea")
        else:
            bytecode = compiler.compile_source(args.source or "app.tea")
        if bytecode is None:
            return 1
        if args.dump:
            print("\n--- Compiled program ---")
            dump_bytecode(bytecode)
            for name, fn in sorted(compiler.functions.items()):
                defaults = len(fn["defaults"])
                print(f"\nfunction {name}({', '.join(fn['params'])})  [line {fn['line']}, {defaults} default(s)]")
                dump_bytecode(fn["body"], 1)
        if args.check or args.dump:
            return 0
        return compiler.run_vm(bytecode)
    except TeaSyntaxError as exc:
        print(format_error(exc, compiler.source_lines))
        return 1
    except KeyboardInterrupt:
        print("\n[Tea] Interrupted.")
        return 130
    except Exception as exc:
        print(f"\n[Tea Internal Error] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
