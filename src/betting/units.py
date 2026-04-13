"""Unit sizing + formatting for public-facing output.

1 unit = 1% of the $10,000 starting bankroll = $100. Standard betting convention.
Keep internal storage in dollars; convert to units at the display layer only.
"""

UNIT_SIZE = 100.0


def to_units(amount: float) -> float:
    return amount / UNIT_SIZE


def fmt_u(amount: float, signed: bool = False) -> str:
    u = amount / UNIT_SIZE
    precision = 2 if abs(u) < 10 else 1
    s = f"{abs(u):.{precision}f}u"
    if signed:
        return ("+" if u >= 0 else "-") + s
    return ("-" + s) if u < 0 else s


def fmt_ud(amount: float, signed: bool = False) -> str:
    """Units-first with dollars in parens: '6.08u ($608)' or '+4.2u (+$421)'."""
    d = f"${abs(amount):,.0f}"
    if signed:
        d = ("+" if amount >= 0 else "-") + d
    elif amount < 0:
        d = "-" + d
    return f"{fmt_u(amount, signed=signed)} ({d})"
