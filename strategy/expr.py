"""The v3 expression language: text in, AST out.

WHY THIS EXISTS
---------------
v2 conditions compare one operand to a constant or to another operand. That
models "RSI above 55" perfectly and cannot model `level = (B * C) / A` at all
— not because the strategy is hard, but because there is no arithmetic in the
grammar. The audit's three prescriptions for v3 (expressions, named variables,
states) all rest on this module: variables need something to hold, and state
transitions need something to test.

WHY NOT eval()
--------------
Strategy text arrives from a database row or pasted from an AI. Handing that
to Python is arbitrary code execution against the machine holding the trading
credentials, and no amount of blacklisting makes it safe. So this is a real
tokenizer and a real parser producing an AST of dataclasses defined here.
Nothing a strategy document contains ever reaches the interpreter.

WHY THE GRAMMAR HAS NO FUTURE
-----------------------------
`close[1]` reads one closed bar back. There is deliberately no syntax that
reads forward: the offset must be a non-negative integer LITERAL, so
`close[-1]` fails at parse time and `close[n]` is refused outright because a
computed offset cannot be proven non-negative before it runs.

This is the load-bearing design choice in the whole language. Look-ahead bias
is the failure that quietly invents an edge and survives every test you would
think to run, so the grammar is built to make it unrepresentable rather than
merely discouraged.

Pure module: stdlib only, no pandas, no I/O. Evaluation lives in signals.py,
the same split vocabulary.py and parse.py already follow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Union

# How many bars back an expression may read. Same reasoning as
# vocabulary.MAX_OFFSET: a typo guard, not a modelling limit.
MAX_OFFSET = 500


class ExpressionError(ValueError):
    """An expression could not be parsed.

    Carries the offending text and, where known, the character position —
    these expressions are frequently AI-generated, and "syntax error" alone
    gives the reader nothing to act on.
    """


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Literal:
    """A number. Always float: the language has one numeric type."""

    value: float


@dataclass(frozen=True)
class Name:
    """A dotted path — ('close',) or ('prev_day', 'high').

    Resolution is deliberately NOT done here. What names exist depends on the
    evaluation namespace (which variables a state machine has declared), and
    the parser has no business knowing that.
    """

    path: tuple[str, ...]


@dataclass(frozen=True)
class Call:
    """A function call — rsi(14), swing.low(20)."""

    path: tuple[str, ...]
    args: tuple["Expr", ...]


@dataclass(frozen=True)
class Offset:
    """`operand[bars]` — the value `bars` closed bars ago.

    `bars` is an int, never an expression, and never negative. Both are
    enforced by the parser; see the module docstring.
    """

    operand: "Expr"
    bars: int


@dataclass(frozen=True)
class Unary:
    op: str          # '-' or 'not'
    operand: "Expr"


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Expr"
    right: "Expr"


Expr = Union[Literal, Name, Call, Offset, Unary, Binary]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Order matters: two-character operators must be tried before the
# one-character ones, or '<=' tokenizes as '<' followed by a stray '='.
#
# A name may not START with an underscore. Nothing in this language needs one,
# and the rule costs nothing while making `close.__class__` — which is
# otherwise a perfectly well-formed dotted path — impossible to write. The
# grammar cannot reach Python attributes anyway, but a language whose parser
# happily accepts dunder paths invites someone to wire it to getattr later.
_TOKEN_RE = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<number>\d+\.\d+|\d+)
    | (?P<name>[A-Za-z][A-Za-z0-9_]*)
    | (?P<op><=|>=|==|!=|[<>+\-*/])
    | (?P<punct>[()\[\],.])
    """,
    re.VERBOSE,
)

# Word operators are tokenized as names and reclassified here, so `android`
# is not mistaken for `and` followed by `roid`.
_WORD_OPERATORS = {"and", "or", "not"}


@dataclass(frozen=True)
class _Token:
    kind: str    # 'number' | 'name' | 'op' | 'punct' | 'end'
    text: str
    pos: int     # 0-based index into the source, for error messages


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(source):
        match = _TOKEN_RE.match(source, pos)
        if match is None:
            raise ExpressionError(
                f"unexpected character {source[pos]!r} at position {pos} "
                f"in expression: {source.strip()!r}"
            )
        kind = match.lastgroup
        text = match.group()
        if kind != "space":
            if kind == "name" and text in _WORD_OPERATORS:
                kind = "op"
            tokens.append(_Token(kind=kind, text=text, pos=pos))
        pos = match.end()
    tokens.append(_Token(kind="end", text="", pos=len(source)))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Binary operator precedence, loosest first. Every level is left-associative,
# which is what makes `10 - 3 - 2` mean 5 rather than 9.
# A marker level rather than a set of operators: `not` is prefix, so it needs
# its own parse step, but it must sit at this exact point in the chain.
_NOT_LEVEL: frozenset[str] = frozenset({"not"})

_PRECEDENCE: tuple[frozenset[str], ...] = (
    frozenset({"or"}),
    frozenset({"and"}),
    _NOT_LEVEL,
    frozenset({"<", ">", "<=", ">=", "==", "!="}),
    frozenset({"+", "-"}),
    frozenset({"*", "/"}),
)


class _Parser:
    def __init__(self, source: str) -> None:
        self._source = source
        self._tokens = _tokenize(source)
        self._index = 0

    # -- token helpers ------------------------------------------------------

    @property
    def _current(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._current
        self._index += 1
        return token

    def _accept(self, kind: str, text: str | None = None) -> _Token | None:
        token = self._current
        if token.kind == kind and (text is None or token.text == text):
            return self._advance()
        return None

    def _expect(self, kind: str, text: str, what: str) -> _Token:
        token = self._accept(kind, text)
        if token is None:
            raise self._error(f"expected {what}", self._current)
        return token

    def _error(self, message: str, token: _Token) -> ExpressionError:
        where = (
            "at end of expression" if token.kind == "end"
            else f"at {token.text!r} (position {token.pos})"
        )
        return ExpressionError(
            f"{message} {where} in expression: {self._source.strip()!r}"
        )

    # -- grammar ------------------------------------------------------------

    def parse(self) -> Expr:
        if self._current.kind == "end":
            raise ExpressionError("expression is empty")
        tree = self._parse_binary(0)
        if self._current.kind != "end":
            raise self._error("unexpected token", self._current)
        return tree

    def _parse_binary(self, level: int) -> Expr:
        if level >= len(_PRECEDENCE):
            return self._parse_unary()
        # `not` sits between `and` and comparison, as it does in Python and in
        # English: `not close > 105` denies the comparison, it does not negate
        # `close` and then compare the result. Putting it with unary minus
        # instead would parse that as `(not close) > 105` — which is still a
        # valid expression, so nothing would error; it would just quietly mean
        # something else.
        if _PRECEDENCE[level] is _NOT_LEVEL:
            return self._parse_not()
        operators = _PRECEDENCE[level]
        left = self._parse_binary(level + 1)
        while self._current.kind == "op" and self._current.text in operators:
            op = self._advance().text
            right = self._parse_binary(level + 1)
            left = Binary(op, left, right)
        return left

    def _parse_not(self) -> Expr:
        level = _PRECEDENCE.index(_NOT_LEVEL)
        if self._current.kind == "op" and self._current.text == "not":
            self._advance()
            return Unary("not", self._parse_not())
        return self._parse_binary(level + 1)

    def _parse_unary(self) -> Expr:
        token = self._current
        if token.kind == "op" and token.text == "-":
            self._advance()
            return Unary("-", self._parse_unary())
        return self._parse_postfix()

    def _parse_postfix(self) -> Expr:
        node = self._parse_primary()
        # Only ONE offset may be applied. `close[1][1]` would be ambiguous
        # about which timeframe each step refers to once higher-timeframe
        # names exist, and writing `close[2]` is clearer regardless.
        if self._accept("punct", "["):
            node = Offset(node, self._parse_offset_literal())
            self._expect("punct", "]", "']' to close the offset")
        return node

    def _parse_offset_literal(self) -> int:
        token = self._current
        # A leading '-' is caught here rather than by the number rule so the
        # message can say what is actually wrong instead of "expected number".
        if token.kind == "op" and token.text == "-":
            raise self._error(
                "a negative offset would read a bar that has not closed yet, "
                "which is look-ahead bias; offsets must be >= 0",
                token,
            )
        if token.kind != "number":
            raise self._error(
                "an offset must be a whole-number literal, so it can be "
                "checked for a future read before the strategy ever runs",
                token,
            )
        self._advance()
        if "." in token.text:
            raise ExpressionError(
                f"offset {token.text} must be a whole number of bars, not a "
                f"fraction, in expression: {self._source.strip()!r}"
            )
        bars = int(token.text)
        if bars > MAX_OFFSET:
            raise ExpressionError(
                f"offset {bars} exceeds the maximum of {MAX_OFFSET} bars. "
                "This is almost always an indicator period in the wrong "
                f"place, in expression: {self._source.strip()!r}"
            )
        return bars

    def _parse_primary(self) -> Expr:
        token = self._current

        if self._accept("punct", "("):
            inner = self._parse_binary(0)
            self._expect("punct", ")", "')' to close the group")
            return inner

        if token.kind == "number":
            self._advance()
            return Literal(float(token.text))

        if token.kind == "name":
            path = [self._advance().text]
            while self._accept("punct", "."):
                part = self._accept("name")
                if part is None:
                    raise self._error("expected a name after '.'", self._current)
                path.append(part.text)
            if self._accept("punct", "("):
                return Call(tuple(path), self._parse_arguments())
            return Name(tuple(path))

        raise self._error("expected a value", token)

    def _parse_arguments(self) -> tuple[Expr, ...]:
        args: list[Expr] = []
        if self._accept("punct", ")"):
            return ()
        while True:
            args.append(self._parse_binary(0))
            if self._accept("punct", ","):
                continue
            self._expect("punct", ")", "')' to close the argument list")
            return tuple(args)


def parse_expression(source: str) -> Expr:
    """Parse `source` into an AST, or raise ExpressionError explaining why not.

    The returned tree is data. Evaluating it is a separate step that decides
    which names are legal, so the same expression can be validated against a
    strategy's declared variables without being run.
    """
    if not isinstance(source, str):
        raise ExpressionError(
            f"an expression must be text, got {type(source).__name__}"
        )
    if not source.strip():
        raise ExpressionError("expression is empty")
    return _Parser(source).parse()


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def walk(node: Expr):
    """Yield every node in the tree, parents before children."""
    yield node
    if isinstance(node, (Unary,)):
        yield from walk(node.operand)
    elif isinstance(node, Offset):
        yield from walk(node.operand)
    elif isinstance(node, Binary):
        yield from walk(node.left)
        yield from walk(node.right)
    elif isinstance(node, Call):
        for arg in node.args:
            yield from walk(arg)


def names_used(node: Expr) -> set[tuple[str, ...]]:
    """Every dotted name the expression reads.

    Used to check an expression against the namespace it will run in — which
    is how an undefined variable becomes a validation error at save time
    rather than a NaN at backtest time.
    """
    return {n.path for n in walk(node) if isinstance(n, Name)}


def calls_used(node: Expr) -> set[tuple[str, ...]]:
    """Every function the expression calls."""
    return {n.path for n in walk(node) if isinstance(n, Call)}


def max_offset(node: Expr) -> int:
    """The furthest back this expression reads, in bars.

    Feeds the warm-up calculation: a strategy that reads 20 bars back cannot
    produce a signal until bar 21, and acting earlier means acting on NaN.
    """
    return max((n.bars for n in walk(node) if isinstance(n, Offset)), default=0)
