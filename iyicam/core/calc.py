"""Numbers typed as sums: ``12+3.5``, ``(100-8)/2``, ``2*7,5`` (a comma is a decimal point like the dot).

Only numbers, ``+ - * / ^`` (power) and brackets are understood: nothing is run, so a number box can be given anything."""
from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"\s*(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|[-+*/^()×÷x:])")


def evaluate(text: str) -> float | None:
    """The value of ``text``, or ``None`` when it is not a number or a sum."""
    if not isinstance(text, str):
        return None
    s = text.strip().replace(",", ".").replace("−", "-").replace("·", "*")
    if not s:
        return None
    toks, i = [], 0
    while i < len(s):
        m = _TOKEN.match(s, i)
        if not m:
            return None
        t = m.group(1)
        toks.append({"×": "*", "x": "*", "÷": "/", ":": "/"}.get(t, t))
        i = m.end()
        while i < len(s) and s[i] == " ":
            i += 1
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def expr():
        v = term()
        while peek() in ("+", "-"):
            v = v + term() if take() == "+" else v - term()
        return v

    def term():
        v = power()
        while peek() in ("*", "/"):
            if take() == "*":
                v = v * power()
            else:
                d = power()
                if d == 0:
                    raise ZeroDivisionError
                v = v / d
        return v

    def power():
        v = unary()
        if peek() == "^":
            take()
            e = power()
            if abs(e) > 64:
                raise OverflowError
            v = v ** e
        return v

    def unary():
        t = peek()
        if t == "-":
            take()
            return -unary()
        if t == "+":
            take()
            return unary()
        return atom()

    def atom():
        t = take() if peek() is not None else None
        if t is None:
            raise ValueError
        if t == "(":
            v = expr()
            if peek() != ")":
                raise ValueError
            take()
            return v
        if t in "+-*/^)":
            raise ValueError
        return float(t)

    try:
        v = expr()
        if pos != len(toks) or isinstance(v, complex) or not math.isfinite(v):
            return None
        return float(v)
    except (ValueError, ZeroDivisionError, OverflowError, IndexError):
        return None


def is_sum(text: str) -> bool:
    """Whether ``text`` is more than a plain number (a sum to be worked out)."""
    return evaluate(text) is not None and re.fullmatch(r"\s*[-+]?\s*(\d+[.,]?\d*|[.,]\d+)([eE][+-]?\d+)?\s*", text) is None
