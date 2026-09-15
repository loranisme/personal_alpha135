"""Performance-blind, conservative descriptions of local measurement relationships.

Tags describe observations, never validation or causal evidence. This module neither
executes expressions nor changes their definitions, direction, or approval status.
"""
from __future__ import annotations

import ast

_FIELDS = {'open', 'high', 'low', 'close', 'volume', 'vwap', 'returns', 'benchmark_returns'}
_LABELS = {
    'benchmark_beta_change': '相对基准的短长窗口市场暴露差',
    'momentum': '价格延续', 'mean_reversion': '价格回归近期水平',
    'realized_risk': '已实现收益波动',
    'price_volume_covariation': '价格与成交量的共变',
    'trading_activity': '成交量相对近期水平',
    'intraday_return': '开盘至收盘收益', 'overnight_return': '前收盘至开盘收益',
    'range_position': '收盘在日内高低区间的位置',
    'vwap_deviation': '收盘相对成交均价的偏离',
    'illiquidity_proxy': '单位估计成交金额对应的绝对价格变化',
}


def _names(node: ast.AST) -> set[str]:
    calls = {id(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and id(n) not in calls}


def _call(node: ast.AST, *names: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names


def _name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _normalize(node: ast.AST) -> ast.AST:
    """Normalize functional arithmetic only; retain all measurement operators."""
    operators = {'divide': ast.Div, 'subtract': ast.Sub, 'add': ast.Add, 'multiply': ast.Mult}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == 'reverse' and len(node.args) == 1 and not node.keywords:
            return ast.UnaryOp(ast.USub(), _normalize(node.args[0]))
        if node.func.id in operators and len(node.args) == 2 and not node.keywords:
            return ast.BinOp(_normalize(node.args[0]), operators[node.func.id](), _normalize(node.args[1]))
    for field, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            setattr(node, field, _normalize(value))
        elif isinstance(value, list):
            setattr(node, field, [_normalize(v) if isinstance(v, ast.AST) else v for v in value])
    return node


def _number(node: ast.AST):
    if isinstance(node, ast.Constant) and type(node.value) in (float, int):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _number(node.operand)
        return -value if value is not None else None
    return None


def _beta_window(node: ast.AST):
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
        return None
    numerator, denominator = node.left, node.right
    if not (_call(numerator, 'ts_covariance') and _call(denominator, 'ts_covariance')):
        return None
    if len(numerator.args) != 3 or len(denominator.args) != 3:
        return None
    if not all(_name(arg, 'benchmark_returns') for arg in denominator.args[:2]):
        return None
    window = _number(numerator.args[2])
    if window is None or window <= 1 or window != _number(denominator.args[2]):
        return None
    stock, benchmark = numerator.args[:2]
    if not _name(benchmark, 'benchmark_returns'):
        stock, benchmark = benchmark, stock
    if not _name(benchmark, 'benchmark_returns'):
        return None
    if _name(stock, 'returns'):
        return window
    # Require the actual close-to-close return relationship, not a naked price.
    expected = ast.parse('close / ts_delay(close, 1) - 1', mode='eval').body
    return window if ast.dump(stock) == ast.dump(expected) else None


def describe_local_expression(expression: str) -> dict:
    """Return deterministic Chinese measurement descriptions; no data are read.

    Unrecognized syntax/fields and nonlinear combinations deliberately retain an
    ambiguous interpretation. Recognized component tags do not establish a causal
    interpretation of the enclosing composite expression.
    """
    limitations = ['仅根据公式关系推断，未读取收益或验证预测能力；不改变公式或批准状态。']
    tags: set[str] = set()
    try:
        root = _normalize(ast.parse(expression.strip(), mode='eval').body)
    except (SyntaxError, ValueError, RecursionError):
        root = None
    negative = False
    if root is not None:
        top = root
        while True:
            if _call(top, 'rank', 'zscore', 'winsorize', 'ts_mean', 'ts_rank') and top.args:
                top = top.args[0]
            elif isinstance(top, ast.UnaryOp) and isinstance(top.op, ast.USub):
                negative = not negative
                top = top.operand
            elif isinstance(top, ast.BinOp) and isinstance(top.op, ast.Sub) and _number(top.left) is not None:
                negative = not negative
                top = top.right
            elif isinstance(top, ast.BinOp) and isinstance(top.op, ast.Mult) and _number(top.left) is not None and _number(top.left) != 0:
                negative ^= _number(top.left) < 0
                top = top.right
            elif isinstance(top, ast.BinOp) and isinstance(top.op, (ast.Mult, ast.Div)) and _number(top.right) is not None and _number(top.right) != 0:
                negative ^= _number(top.right) < 0
                top = top.left
            else:
                break
        valid = _names(root) <= _FIELDS
    else:
        valid = False
    complex_combination = False
    reciprocal_measurement = False
    if valid:
        for node in ast.walk(root):
            fields = _names(node)
            if isinstance(node, ast.BinOp):
                if isinstance(node.op, ast.Pow) or (isinstance(node.op, ast.Mult) and (_number(node.left) == 0 or _number(node.right) == 0)):
                    complex_combination = True
                if isinstance(node.op, ast.Div) and _number(node.left) is not None and _names(node.right):
                    reciprocal_measurement = True
                    complex_combination = True
                if isinstance(node.op, (ast.Add, ast.Sub)) and _names(node.left) and _names(node.right):
                    if any(isinstance(n, (ast.Call, ast.BinOp)) for n in ast.walk(node.left)) and any(isinstance(n, (ast.Call, ast.BinOp)) for n in ast.walk(node.right)):
                        complex_combination = True
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
                first, second = _beta_window(node.left), _beta_window(node.right)
                if first is not None and second is not None and first != second:
                    tags.add('benchmark_beta_change')
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Pow)):
                if _number(node.left) is None and _number(node.right) is None:
                    complex_combination = True
            if _call(node, 'abs', 'sign', 'if_else'):
                complex_combination = True
            if _call(node, 'ts_std_dev', 'ts_variance') and node.args:
                if 'returns' in fields or any(_call(n, 'ts_delay') for n in ast.walk(node)):
                    tags.add('realized_risk')
            if _call(node, 'ts_corr', 'ts_covariance') and len(node.args) >= 2:
                a, b = map(_names, node.args[:2])
                prices = {'open', 'close', 'high', 'low', 'vwap', 'returns'}
                if ('volume' in a and b & prices) or ('volume' in b and a & prices):
                    tags.add('price_volume_covariation')
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
                continue
            left, right = node.left, node.right
            if _name(left, 'close') and _name(right, 'open'):
                tags.add('intraday_return')
            if _name(left, 'open') and _call(right, 'ts_delay') and right.args and _name(right.args[0], 'close'):
                tags.add('overnight_return')
            if _name(left, 'close') and _call(right, 'ts_delay') and right.args and _name(right.args[0], 'close'):
                tags.add('mean_reversion' if negative else 'momentum')
            if _call(left, 'ts_mean') and left.args and _name(left.args[0], 'close') and _name(right, 'close'):
                tags.add('momentum' if negative else 'mean_reversion')
            if _names(left) == {'volume'} and _names(right) == {'volume'} and (_call(left, 'ts_mean') or _call(right, 'ts_mean')):
                tags.add('trading_activity')
            if {'high', 'low'} <= _names(right) and 'close' in _names(left):
                tags.add('range_position')
            if 'vwap' in fields and 'close' in fields and fields <= {'vwap', 'close'}:
                tags.add('vwap_deviation')
            if _call(left, 'abs') and 'volume' in _names(right) and _names(right) & {'close', 'open', 'vwap'}:
                tags.add('illiquidity_proxy')
        if 'returns' in _names(root) and not tags and not complex_combination:
            tags.add('mean_reversion' if negative else 'momentum')
        # Embedded return calculations describe inputs to these mechanisms, not
        # an independent price-continuation hypothesis.
        if tags & {'realized_risk', 'illiquidity_proxy', 'price_volume_covariation', 'benchmark_beta_change'}:
            tags -= {'momentum', 'mean_reversion'}
    if reciprocal_measurement:
        tags -= {'momentum', 'mean_reversion'}
    ambiguous = not tags or complex_combination or len(tags) > 1
    labels = '、'.join(_LABELS[t] for t in sorted(tags))
    if not tags:
        measurement = '未识别出有充分结构证据的本地经济观测关系。'
    else:
        measurement = f'观测{labels}；外层取负方向为{negative}。具体窗口和完整排序关系以公式为准。'
    if ambiguous:
        hypothesis = '仅描述可识别的观测组成；完整组合的排序含义与预测方向待验证，不推断因果。'
        limitations.append('复杂组合或未识别结构不能由局部标签推出整体方向。')
    else:
        hypothesis = f'将{labels}作为可证伪的排序依据；是否预测后续表现尚待独立检验。'
    if tags & {'momentum', 'mean_reversion', 'realized_risk', 'overnight_return'}:
        limitations.append('需固定复权与公司行动处理；原始价格变化不等于总收益。')
    if 'illiquidity_proxy' in tags:
        limitations.append('成交金额为日线估计，不能代表实际点差、订单流或市场深度。')
    if 'benchmark_beta_change' in tags:
        limitations.append('本地协方差比值定义；需同步基准收益、非零基准方差及完整长窗，不继承原平台风险定义。')
    if 'vwap_deviation' in tags:
        limitations.append('需要真实 VWAP 输入，不能以典型价格冒充。')
    return {'mechanism_tags': sorted(tags) or ['unclassified'], 'hypothesis_text': hypothesis,
            'measurement_text': measurement, 'interpretation_status': 'AMBIGUOUS' if ambiguous else 'INFERRED',
            'limitations': limitations}
