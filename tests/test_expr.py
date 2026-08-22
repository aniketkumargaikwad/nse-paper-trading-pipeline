"""The v3 expression language: text -> AST.

This is the foundation the rest of v3 stands on. The audit's verdict on v2 was
that it "cannot express state", but the first wall you hit is smaller and more
basic: there is no arithmetic. `level = (B * C) / A` is not a hard strategy,
it is a hard *sentence*, because a v2 condition can only ever compare one
operand to a constant or another operand.

Two properties are non-negotiable and are tested here rather than assumed:

* **No eval.** The parser builds an AST this module defines. Nothing in a
  strategy document ever reaches the Python interpreter, because a strategy
  document is data that arrives from a database row or an AI paste.
* **No look-ahead is representable.** There is no syntax for a future bar.
  `close[1]` reads backward; `close[-1]` is a parse error, not a runtime one.
  Bias you cannot write down is bias you cannot ship.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.expr import (  # noqa: E402
    Binary,
    Call,
    ExpressionError,
    Literal,
    Name,
    Offset,
    Unary,
    parse_expression,
)


# --- literals and names -----------------------------------------------------


def test_a_number_parses() -> None:
    assert parse_expression("42") == Literal(42.0)


def test_a_decimal_parses() -> None:
    assert parse_expression("1.5") == Literal(1.5)


def test_a_bare_name_parses() -> None:
    assert parse_expression("close") == Name(("close",))


def test_a_dotted_name_parses() -> None:
    assert parse_expression("prev_day.high") == Name(("prev_day", "high"))


def test_a_deeply_dotted_name_parses() -> None:
    assert parse_expression("daily.candle.is_bullish") == Name(
        ("daily", "candle", "is_bullish")
    )


# --- arithmetic and precedence ---------------------------------------------


def test_addition_parses() -> None:
    assert parse_expression("1 + 2") == Binary("+", Literal(1.0), Literal(2.0))


def test_multiplication_binds_tighter_than_addition() -> None:
    """1 + 2 * 3 is 1 + (2 * 3), never (1 + 2) * 3."""
    assert parse_expression("1 + 2 * 3") == Binary(
        "+", Literal(1.0), Binary("*", Literal(2.0), Literal(3.0))
    )


def test_parentheses_override_precedence() -> None:
    assert parse_expression("(1 + 2) * 3") == Binary(
        "*", Binary("+", Literal(1.0), Literal(2.0)), Literal(3.0)
    )


def test_subtraction_is_left_associative() -> None:
    """10 - 3 - 2 is (10 - 3) - 2 = 5, not 10 - (3 - 2) = 9."""
    assert parse_expression("10 - 3 - 2") == Binary(
        "-", Binary("-", Literal(10.0), Literal(3.0)), Literal(2.0)
    )


def test_division_is_left_associative() -> None:
    assert parse_expression("8 / 4 / 2") == Binary(
        "/", Binary("/", Literal(8.0), Literal(4.0)), Literal(2.0)
    )


def test_unary_minus_parses() -> None:
    assert parse_expression("-close") == Unary("-", Name(("close",)))


def test_the_audit_abc_expression_parses() -> None:
    """`(B * C) / A` — the expression v2 could not represent at all."""
    tree = parse_expression("(b * c) / a")
    assert tree == Binary(
        "/", Binary("*", Name(("b",)), Name(("c",))), Name(("a",))
    )


# --- comparison and boolean logic ------------------------------------------


@pytest.mark.parametrize("op", ["<", ">", "<=", ">=", "==", "!="])
def test_every_comparison_operator_parses(op: str) -> None:
    assert parse_expression(f"close {op} 100") == Binary(
        op, Name(("close",)), Literal(100.0)
    )


def test_comparison_binds_looser_than_arithmetic() -> None:
    """close > open + 1 compares close against (open + 1)."""
    assert parse_expression("close > open + 1") == Binary(
        ">", Name(("close",)), Binary("+", Name(("open",)), Literal(1.0))
    )


def test_and_binds_tighter_than_or() -> None:
    """a or b and c is a or (b and c)."""
    assert parse_expression("a or b and c") == Binary(
        "or", Name(("a",)), Binary("and", Name(("b",)), Name(("c",)))
    )


def test_and_binds_looser_than_comparison() -> None:
    tree = parse_expression("close > 1 and close < 2")
    assert tree == Binary(
        "and",
        Binary(">", Name(("close",)), Literal(1.0)),
        Binary("<", Name(("close",)), Literal(2.0)),
    )


def test_not_parses() -> None:
    assert parse_expression("not a") == Unary("not", Name(("a",)))


def test_the_audit_confirmation_expression_parses() -> None:
    """`candle.is_bearish and close > prev_day.low` — straight from §12."""
    tree = parse_expression("candle.is_bearish and close > prev_day.low")
    assert tree == Binary(
        "and",
        Name(("candle", "is_bearish")),
        Binary(">", Name(("close",)), Name(("prev_day", "low"))),
    )


# --- calls ------------------------------------------------------------------


def test_a_call_with_one_argument_parses() -> None:
    assert parse_expression("rsi(14)") == Call(("rsi",), (Literal(14.0),))


def test_a_call_with_no_arguments_parses() -> None:
    assert parse_expression("vwap()") == Call(("vwap",), ())


def test_a_dotted_call_parses() -> None:
    """`swing.low(20)` — the structural vocabulary the audit asks for."""
    assert parse_expression("swing.low(20)") == Call(("swing", "low"), (Literal(20.0),))


def test_a_call_with_several_arguments_parses() -> None:
    assert parse_expression("macd(12, 26, 9)") == Call(
        ("macd",), (Literal(12.0), Literal(26.0), Literal(9.0))
    )


def test_an_expression_may_be_an_argument() -> None:
    assert parse_expression("ema(7 + 7)") == Call(
        ("ema",), (Binary("+", Literal(7.0), Literal(7.0)),)
    )


def test_a_call_participates_in_arithmetic() -> None:
    assert parse_expression("atr(14) * 2") == Binary(
        "*", Call(("atr",), (Literal(14.0),)), Literal(2.0)
    )


# --- offsets: reading backward, and only backward ---------------------------


def test_offset_parses() -> None:
    assert parse_expression("close[1]") == Offset(Name(("close",)), 1)


def test_offset_zero_parses() -> None:
    assert parse_expression("close[0]") == Offset(Name(("close",)), 0)


def test_offset_on_a_dotted_name_parses() -> None:
    assert parse_expression("prev_day.high[2]") == Offset(
        Name(("prev_day", "high")), 2
    )


def test_offset_on_a_call_parses() -> None:
    """The previous bar's RSI."""
    assert parse_expression("rsi(14)[1]") == Offset(
        Call(("rsi",), (Literal(14.0),)), 1
    )


def test_negative_offset_is_a_parse_error() -> None:
    """The whole point. A future bar must be unwriteable, not merely unwise."""
    with pytest.raises(ExpressionError) as exc:
        parse_expression("close[-1]")
    assert "offset" in str(exc.value).lower()


def test_non_integer_offset_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        parse_expression("close[1.5]")


def test_offset_by_an_expression_is_rejected() -> None:
    """A computed offset cannot be checked for a future read at parse time."""
    with pytest.raises(ExpressionError):
        parse_expression("close[n]")


def test_absurd_offset_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        parse_expression("close[999999]")


# --- error reporting --------------------------------------------------------


def test_empty_expression_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        parse_expression("   ")


def test_unbalanced_parenthesis_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        parse_expression("(close > 1")


def test_trailing_operator_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        parse_expression("close >")


def test_two_operands_in_a_row_is_rejected() -> None:
    with pytest.raises(ExpressionError):
        parse_expression("close 100")


def test_an_unknown_character_is_rejected_with_its_position() -> None:
    with pytest.raises(ExpressionError) as exc:
        parse_expression("close $ 1")
    message = str(exc.value)
    assert "$" in message
    assert "position 6" in message


def test_error_names_the_expression_it_came_from() -> None:
    """These arrive from AI pastes; a bare 'syntax error' is unactionable."""
    with pytest.raises(ExpressionError) as exc:
        parse_expression("close >")
    assert "close >" in str(exc.value)


# --- no code execution ------------------------------------------------------


def test_python_syntax_is_not_accepted() -> None:
    """A reminder that this is a parser, not a sandbox around eval()."""
    for hostile in [
        "__import__('os').system('echo pwned')",
        "close.__class__",
        "lambda: 1",
        "[x for x in range(10)]",
        "close if close else 1",
    ]:
        with pytest.raises(ExpressionError):
            parse_expression(hostile)


def test_whitespace_is_insignificant() -> None:
    assert parse_expression("close>1") == parse_expression("  close   >   1  ")
