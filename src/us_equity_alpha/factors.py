"""Non-evaluating-AST factor execution for the frozen MVP operator subset."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from .operators import cs_rank, linear_decay, price_delta, ts_delay, ts_mean, ts_rank, ts_std_dev


class UnsupportedExpression(ValueError):
    pass


FUNCTIONS = {
    "rank": cs_rank,
    "ts_delay": ts_delay,
    "ts_delta": price_delta,
    "ts_mean": ts_mean,
    "ts_rank": ts_rank,
    "ts_std_dev": ts_std_dev,
    "abs": np.abs,
    "log": np.log,
    "sign": np.sign,
    "reverse": lambda value: -value,
    "add": lambda left, right: left + right,
    "subtract": lambda left, right: left - right,
    "multiply": lambda left, right: left * right,
    "divide": lambda left, right: left / right,
    "max": lambda left, right: np.maximum(left, right),
}


def _normalize(expression: str) -> str:
    without_comments = re.sub(r"/\*.*?\*/", "", expression, flags=re.DOTALL)
    return " ".join(without_comments.split())


class _Evaluator:
    def __init__(self, inputs: Mapping[str, pd.DataFrame]):
        self.values: dict[str, Any] = dict(inputs)

    def module(self, tree: ast.Module) -> Any:
        result: Any = None
        for statement in tree.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                result = self.node(statement.value)
                self.values[statement.targets[0].id] = result
            elif isinstance(statement, ast.Expr):
                result = self.node(statement.value)
            else:
                raise UnsupportedExpression("UNSUPPORTED_STATEMENT")
        return result

    def node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Name):
            if node.id in self.values:
                return self.values[node.id]
            if node.id in {"true", "True"}:
                return True
            if node.id in {"false", "False"}:
                return False
            raise UnsupportedExpression(f"UNKNOWN_IDENTIFIER:{node.id}")
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, bool)):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self.node(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = self.node(node.left), self.node(node.right)
            operations = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
                ast.Pow: lambda: left ** right,
            }
            operation = operations.get(type(node.op))
            if operation is None:
                raise UnsupportedExpression(f"UNSUPPORTED_BINARY_OPERATOR:{type(node.op).__name__}")
            return operation()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = FUNCTIONS.get(node.func.id)
            if function is None:
                raise UnsupportedExpression(f"UNSUPPORTED_OPERATOR:{node.func.id}")
            if node.keywords:
                raise UnsupportedExpression("KEYWORD_ARGUMENTS_NOT_SUPPORTED_IN_MVP")
            return function(*(self.node(argument) for argument in node.args))
        raise UnsupportedExpression(f"UNSUPPORTED_NODE:{type(node).__name__}")


def evaluate_factor(
    expression: str,
    data: Mapping[str, pd.DataFrame],
    settings: Mapping[str, Any],
) -> pd.DataFrame:
    """Evaluate a frozen subset without Python eval/exec or implicit filling."""
    try:
        delay = int(settings["delay"])
        decay = int(settings.get("decay", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise UnsupportedExpression("INVALID_DELAY_OR_DECAY_SETTING") from exc
    if delay < 0 or decay < 0:
        raise UnsupportedExpression("INVALID_DELAY_OR_DECAY_SETTING")
    if str(settings.get("neutralization", "")).upper() != "NONE":
        raise UnsupportedExpression("MVP_NEUTRALIZATION_NOT_SUPPORTED")
    if not data:
        raise UnsupportedExpression("NO_INPUT_DATA")
    shapes = {(tuple(frame.index), tuple(frame.columns)) for frame in data.values()}
    if len(shapes) != 1 or not all(isinstance(frame, pd.DataFrame) for frame in data.values()):
        raise UnsupportedExpression("INPUT_ALIGNMENT_MISMATCH")

    delayed = {name: frame.shift(delay) if delay else frame.copy() for name, frame in data.items()}
    try:
        tree = ast.parse(_normalize(expression), mode="exec")
    except (SyntaxError, ValueError) as exc:
        raise UnsupportedExpression("PARSE_ERROR") from exc
    result = _Evaluator(delayed).module(tree)
    if not isinstance(result, pd.DataFrame):
        raise UnsupportedExpression("FACTOR_RESULT_NOT_MATRIX")
    return linear_decay(result, decay) if decay > 1 else result
