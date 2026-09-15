"""Performance-blind reconstruction of measurable economic components.

Source expressions are evidence only. Local risk/missing policies are explicit;
separable components may be retained, but missing operands of ratios, products
or spreads are never manufactured from prices.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .factors import FUNCTIONS
from .economic_descriptions import describe_local_expression
from .proxy_converter import ALLOWED_CAPABILITY_STATES

POLICY = {
    "policy_id": "local_economic_components_v1",
    "formula_parity_required": False,
    "performance_inputs_used": False,
    "local_settings": {"delay": 1, "decay": 0, "neutralization": "NONE"},
    "timing": "One-session lag of completed daily observations; execution contract unchanged.",
    "group_policy": "Global rank instead of source group rank; group demeaning omitted with explicit relation loss.",
    "missing_policy": "No source backfill; missing observations remain missing.",
    "component_policy": "Whole measurable expression first; otherwise only separable additive ranked terms. Never mine inside a missing ratio/product/spread.",
    "parameter_policy": "Source economic horizons retained as unselected candidates; no parameter search or performance selection.",
    "family_policy": "Structural economic signature ignores numeric horizons; broad mechanism tags retained separately. Not a count of proven independent mechanisms.",
    "admitted_usage_tier": "DIAGNOSTIC_ONLY",
    "automatic_release_activation": False,
}

GROUPS = {"industry", "subindustry", "sector", "market"}
PRICE_FIELDS = {"open", "high", "low", "close", "volume", "vwap", "returns", "adv20"}
WRAPPERS = {"rank", "group_rank", "group_neutralize", "ts_rank", "ts_mean", "ts_backfill", "winsorize", "zscore", "ts_zscore"}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()


def _capability_identity(capability: Mapping[str, Any]) -> dict[str, Any]:
    """Hash field semantics and provider bindings, excluding sample evidence."""
    keys = (
        "capability_id",
        "binding",
        "definition",
        "providers",
        "semantic_status",
        "source_value_parity",
    )
    return {key: copy.deepcopy(capability.get(key)) for key in keys}


def _names(node: ast.AST) -> set[str]:
    calls = {n.func.id for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    return {n.id for n in ast.walk(node) if isinstance(n,ast.Name)} - calls - {"true","false","True","False"}


def _parse(expression: str) -> ast.AST:
    text = re.sub(r"/\*.*?\*/", "", expression, flags=re.S)
    text = " ".join(text.split())
    tree = ast.parse(text, mode="exec")
    if len(list(ast.walk(tree))) > 2000:
        raise ValueError("SOURCE_TOO_COMPLEX")
    env: dict[str,ast.AST] = {}
    class Resolve(ast.NodeTransformer):
        def visit_Name(self,node):
            return copy.deepcopy(env.get(node.id,node))
    result = None
    for stmt in tree.body:
        if isinstance(stmt,ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name):
            result = Resolve().visit(copy.deepcopy(stmt.value))
            env[stmt.targets[0].id] = result
        elif isinstance(stmt,ast.Expr):
            result = Resolve().visit(copy.deepcopy(stmt.value))
        else:
            raise ValueError("UNSUPPORTED_SOURCE_STATEMENT")
        if len(list(ast.walk(result))) > 4000:
            raise ValueError("SOURCE_TOO_COMPLEX")
    if result is None:
        raise ValueError("EMPTY_SOURCE")
    forbidden = (ast.Attribute,ast.Subscript,ast.Lambda,ast.ListComp,ast.Dict,ast.List,ast.JoinedStr)
    if any(isinstance(n,forbidden) for n in ast.walk(result)):
        raise ValueError("UNSAFE_SOURCE")
    return result


def _call(name: str, *args: ast.AST) -> ast.Call:
    return ast.Call(func=ast.Name(id=name,ctx=ast.Load()),args=list(args),keywords=[])


def _translate(node: ast.AST, capabilities: Mapping[str,Any], lost: set[str]) -> ast.AST:
    if isinstance(node,ast.Name):
        if node.id in {"true","True","false","False"}:
            return ast.Constant(value=node.id in {"true","True"})
        cap = capabilities.get(node.id,{})
        if cap.get("status") not in ALLOWED_CAPABILITY_STATES or not cap.get("providers"):
            raise ValueError("FIELD_UNAVAILABLE:"+node.id)
        authorized = node.id in PRICE_FIELDS or cap.get("local_formula_authorized") is True
        if not authorized:
            if not re.fullmatch(r"(?:historical_volatility_(30|60|120)|beta_last_(30|360)_days_spy)",node.id):
                raise ValueError("FIELD_SEMANTICS_UNREVIEWED:"+node.id)
        if cap["binding"] != node.id or cap.get("source_value_parity") == "NOT_VERIFIED":
            lost.add("SOURCE_FIELD_PARITY_NOT_CLAIMED:"+node.id)
        return ast.parse(cap["binding"],mode="eval").body
    if isinstance(node,ast.Constant) and isinstance(node.value,(int,float,bool)):
        return copy.deepcopy(node)
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        return ast.UnaryOp(op=copy.deepcopy(node.op),operand=_translate(node.operand,capabilities,lost))
    if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow)):
        return ast.BinOp(left=_translate(node.left,capabilities,lost),op=copy.deepcopy(node.op),right=_translate(node.right,capabilities,lost))
    if isinstance(node,ast.Compare) and len(node.ops)==1:
        return ast.Compare(left=_translate(node.left,capabilities,lost),ops=copy.deepcopy(node.ops),comparators=[_translate(n,capabilities,lost) for n in node.comparators])
    if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name):
        raise ValueError("UNSUPPORTED_SOURCE_NODE")
    name = node.func.id
    if name == "ts_regression":
        raise ValueError("OPERATOR_SEMANTICS_UNVERIFIED:ts_regression")
    if name == "add" and node.keywords:
        if (
            len(node.args) < 2
            or len(node.keywords) != 1
            or node.keywords[0].arg != "filter"
            or not isinstance(node.keywords[0].value, ast.Name)
            or node.keywords[0].value.id not in {"true", "True"}
        ):
            raise ValueError("ADD_FILTER_SEMANTICS_UNREVIEWED")
        lost.add("SOURCE_FILTER_SEMANTICS_REDEFINED_MISSING_PROPAGATES")
        translated = [_translate(argument, capabilities, lost) for argument in node.args]
        result = translated[0]
        for argument in translated[1:]:
            result = ast.BinOp(left=result, op=ast.Add(), right=argument)
        return result
    if name in {"group_rank","group_neutralize"}:
        if len(node.args)!=2 or node.keywords or not isinstance(node.args[1],ast.Name) or node.args[1].id not in GROUPS:
            raise ValueError("UNREVIEWED_GROUP")
        lost.add("SOURCE_GROUP_RELATION_REDEFINED")
        arg = _translate(node.args[0],capabilities,lost)
        return _call("rank",arg) if name=="group_rank" else arg
    if name in {"ts_backfill","winsorize"}:
        if not node.args:
            raise ValueError("INVALID_WRAPPER")
        lost.add("SOURCE_BACKFILL_REMOVED" if name=="ts_backfill" else "SOURCE_WINSORIZATION_REMOVED")
        return _translate(node.args[0],capabilities,lost)
    if name=="ts_zscore" and len(node.args)==2 and not node.keywords:
        arg,window = [_translate(n,capabilities,lost) for n in node.args]
        return ast.BinOp(left=ast.BinOp(left=arg,op=ast.Sub(),right=_call("ts_mean",copy.deepcopy(arg),window)),op=ast.Div(),right=_call("ts_std_dev",copy.deepcopy(arg),copy.deepcopy(window)))
    if name=="zscore" and len(node.args)==1 and not node.keywords:
        lost.add("SOURCE_ZSCORE_REDEFINED_AS_CENTERED_RANK")
        return ast.BinOp(left=_call("rank",_translate(node.args[0],capabilities,lost)),op=ast.Sub(),right=ast.Constant(value=0.5))
    if name not in FUNCTIONS or node.keywords:
        raise ValueError("OPERATOR_UNREVIEWED:"+name)
    return _call(name,*[_translate(n,capabilities,lost) for n in node.args])


def _substantive(node: ast.AST) -> bool:
    """A naked price denominator is not a return/quality mechanism."""
    names = _names(node)
    if names & {"returns","adv20","volume"} or any(x.startswith(("historical_volatility_", "beta_last_")) for x in names):
        return True
    calls = {n.func.id for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    economic = calls & {"ts_delta","ts_mean","ts_sum","ts_std_dev","ts_scale","ts_arg_min","ts_arg_max","ts_corr","ts_covariance","ts_zscore","days_from_last_change","ts_rank"}
    arithmetic = calls & {"add", "subtract", "multiply", "divide"}
    differences = any(isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Sub,ast.Div)) for n in ast.walk(node))
    return bool(names & PRICE_FIELDS and (economic or arithmetic or differences))


def _separable(node: ast.AST) -> list[ast.AST]:
    # Decompose ONLY explicit additive portfolios of ranked components. A
    # subtraction such as IV-HV is an economic contrast, never a portfolio.
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
        return _separable(node.left)+_separable(node.right)
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Mult):
        for constant,term in ((node.left,node.right),(node.right,node.left)):
            if isinstance(constant,ast.Constant) and isinstance(constant.value,(int,float)) and constant.value>0:
                return _separable(term)
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {"rank","group_rank"}:
        return [node]
    return []


def _separable_with_losses(node: ast.AST, wrappers=()) -> list[tuple[ast.AST,set[str]]]:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "hump"
        and len(node.args) == 1
        and not node.keywords
    ):
        return _separable_with_losses(node.args[0], wrappers + (node.func.id,))
    return [
        (term, {f"SOURCE_WRAPPER_NOT_RETAINED:{name}" for name in wrappers})
        for term in _separable(node)
    ]


def _mechanisms(names: set[str], expression: str) -> list[str]:
    tags=[]
    if names & PRICE_FIELDS or any(n.startswith(("historical_volatility_", "beta_last_", "correlation_last_", "systematic_risk_", "unsystematic_risk_")) for n in names):
        tags.append("observed_price_volume_component")
    if any(any(t in n for t in ("income","cash","asset","equity","debt","sales","cap","sharesout","inventory","ebit","bookvalue","receivable")) for n in names):
        tags.append("fundamental_or_valuation_information")
    if any(n.startswith(("anl","est_","analyst")) or "guidance" in n for n in names):
        tags.append("expectations_information")
    if any("implied" in n or "pcr_" in n or "option" in n for n in names):
        tags.append("options_information")
    if any(any(t in n for t in ("news","sentiment","snt_","scl","buzz")) for n in names):
        tags.append("news_or_social_information")
    if names & GROUPS:
        tags.append("peer_relative_relationship")
    return tags or ["unknown"]


def reconstruct_library(source_view: Sequence[Mapping[str,Any]], capabilities: Mapping[str,Any]) -> dict[str,Any]:
    ids=[s.get("alpha_id") for s in source_view]
    if any(not isinstance(x,str) or not x for x in ids) or len(ids)!=len(set(ids)):
        raise ValueError("SOURCE_IDS_INVALID_OR_DUPLICATE")
    cards=[];decisions=[];factors={};families={}
    for source in sorted(source_view,key=lambda s:s["alpha_id"]):
        sid=source["alpha_id"];expr=str(source.get("expression") or "")
        card={"source_alpha_id":sid,"source_definition_hash":source.get("definition_hash"),
              "source_expression":expr,"source_description":source.get("description"),
              "hypothesis_origin":"INFERRED_FROM_FORMULA",
              "semantic_review":"RULE_BASED_INFERENCE_NOT_CAUSALLY_VERIFIED",
              "source_settings":copy.deepcopy(source.get("settings",{})),
              "local_factor_ids":[],"missing_fields":[],"uncertainties":[]}
        reason=[];candidates=[];description_formula_conflict=False
        try:
            node=_parse(expr)
            names=_names(node)
            missing=sorted(n for n in names-GROUPS if capabilities.get(n,{}).get("status") not in ALLOWED_CAPABILITY_STATES)
            card["missing_fields"]=missing
            card["mechanisms"]=_mechanisms(names,expr)
            card["observables"]=sorted(names-GROUPS)
            lost=set()
            try:
                target=_translate(node,capabilities,lost)
                if not _substantive(target):
                    raise ValueError("ECONOMIC_OBSERVABLE_INSUFFICIENT")
                candidates=[(node,target,lost)]
            except ValueError as exc:
                reason.append(str(exc))
                for term, wrapper_losses in _separable_with_losses(node):
                    loss={"SOURCE_COMPOSITE_COMPONENTS_NOT_RETAINED"}|wrapper_losses
                    try:
                        target=_translate(term,capabilities,loss)
                        if _substantive(target):
                            candidates.append((term,target,loss))
                    except ValueError:
                        pass
            # Source commentary conflicting with price evidence stays visible.
            comment=" ".join(re.findall(r"/\*.*?\*/",expr,flags=re.S))+str(source.get("description") or "")
            if comment and any(word in comment.lower() for word in ("cash flow","income","debt","fundamental","earnings")) and not missing:
                description_formula_conflict=True
                card["uncertainties"].append("DESCRIPTION_FORMULA_CONFLICT_REQUIRES_REVIEW")
                card["hypothesis_tracks"]=[
                    {"origin":"DESCRIPTION","status":"DEFERRED_DESCRIPTION_HYPOTHESIS"},
                    {"origin":"FORMULA","status":"RELATED_FORMULA_HYPOTHESIS"},
                ]
                for _,_,loss in candidates:
                    loss.add("DESCRIPTION_FORMULA_CONFLICT_REQUIRES_REVIEW")
                reason.append("DESCRIPTION_FORMULA_CONFLICT_REQUIRES_REVIEW")
        except (ValueError,SyntaxError,TypeError,RecursionError) as exc:
            reason.append(type(exc).__name__ if not isinstance(exc,ValueError) else str(exc))
            card["mechanisms"]=["unknown"]
        relation=[]
        for original,target,lost in candidates:
            expression=ast.unparse(ast.fix_missing_locations(target))
            used=sorted(_names(target))
            description=describe_local_expression(expression)
            # A source setting is historical evidence, never an inherited
            # constraint or justification for a production risk exception.
            settings=copy.deepcopy(POLICY["local_settings"])
            old=source.get("settings",{})
            if str(old.get("neutralization","NONE")).upper()!="NONE":
                lost.add("SOURCE_SETTINGS_RISK_CONTROL_REDEFINED")
            signature={"expression":expression,"settings":settings,
                       "field_bindings":{n:_capability_identity(capabilities[n]) for n in used},
                       "universe_contract":"PROJECT_CALC_UNIVERSE","policy_id":POLICY["policy_id"]}
            provider_sets = [set(capabilities[n].get("providers", [])) for n in used]
            allowed_providers = sorted(set.intersection(*provider_sets)) if provider_sets else []
            digest=_hash(signature);fid="local_reconstruction__"+digest[:16]
            shape=re.sub(r"(?<![A-Za-z_])\d+(?:\.\d+)?","N",expression)
            family="mechanism_shape__"+_hash(shape)[:16]
            semantic=("RELATED_NEW_HYPOTHESIS" if description_formula_conflict
                      else "PARTIAL_INTENT" if lost else "FULL_INTENT")
            f=factors.setdefault(fid,{"local_factor_id":fid,"version":1,"family_id":family,
                "provenance_kind":"LOCAL_RECONSTRUCTION","authoring_method":"REVIEWED_RULE_SET",
                "source_alpha_ids":[],"source_definition_hashes":[],"source_lineage":[],
                "expression":expression,"expression_hash":_hash(expression),"field_bindings":{n:n for n in used},
                "settings":settings,"universe_contract":"PROJECT_CALC_UNIVERSE",
                "allowed_providers":allowed_providers,
                "input_contract":{n:{k:copy.deepcopy(capabilities[n].get(k)) for k in ("definition","binding","providers","historical_pit_verified")} for n in used},
                "semantic_status":semantic,"build_status":"COMPILED","usage_tier":"DIAGNOSTIC_ONLY",
                "brain_value_parity":"NOT_REQUIRED_NOT_CLAIMED","preserved":[],"lost":[],
                "new_exposures":["Local global cross-section; source industry/beta/size risk constraints not reproduced.",
                                 "Raw observed prices; total-return and historical PIT are not certified."],
                "local_hypothesis":description["hypothesis_text"],
                "economic_description":description,
                "economic_observable":expression,"direction_basis":"Signed source component, unvalidated locally; no performance selection.",
                "lineage_hash":digest,"checks":{"performance_blind_conversion":True}})
            if sid not in f["source_alpha_ids"]:
                f["source_alpha_ids"].append(sid);f["source_definition_hashes"].append(source.get("definition_hash"))
                f["source_lineage"].append({"source_alpha_id":sid,"source_definition_hash":source.get("definition_hash"),
                    "retained_source_component":ast.unparse(original),"source_settings":copy.deepcopy(old),
                    "semantic_status":semantic,"lost":sorted(lost)})
            f["lost"]=sorted(set(f["lost"])|lost)
            f["preserved"]=sorted(set(f["preserved"])|{ast.unparse(original)})
            if semantic == "RELATED_NEW_HYPOTHESIS":
                f["semantic_status"] = semantic
            elif lost and f["semantic_status"] != "RELATED_NEW_HYPOTHESIS":
                f["semantic_status"]="PARTIAL_INTENT"
            if fid not in card["local_factor_ids"]:card["local_factor_ids"].append(fid)
            relation.append(semantic)
            fam=families.setdefault(family,{"family_id":family,"structural_signature":shape,"local_factor_ids":[],"source_alpha_ids":[], "economic_description":description})
            if fid not in fam["local_factor_ids"]:fam["local_factor_ids"].append(fid)
            if sid not in fam["source_alpha_ids"]:fam["source_alpha_ids"].append(sid)
        card["status"]="DESIGN_READY" if candidates else "DEFERRED"
        card["uncertainties"]+=reason
        cards.append(card)
        decisions.append({"source_alpha_id":sid,"decision":"RECONSTRUCTED" if candidates else "DEFERRED",
            "local_factor_ids":card["local_factor_ids"],"semantic_status":"RELATED_NEW_HYPOTHESIS" if "RELATED_NEW_HYPOTHESIS" in relation else "PARTIAL_INTENT" if "PARTIAL_INTENT" in relation else "FULL_INTENT" if relation else "UNKNOWN",
            "blockers":[] if candidates else reason or ["NO_OBSERVABLE_COMPONENT"],"missing_fields":card["missing_fields"]})
    reconstructed=sum(bool(c["local_factor_ids"]) for c in cards)
    return {"schema_version":2,"policy":copy.deepcopy(POLICY),"cards":cards,"decisions":decisions,
        "families":list(families.values()),"factors":list(factors.values()),
        "summary":{"source_count":len(cards),"card_count":len(cards),"reconstructed_source_count":reconstructed,
            "deferred_source_count":len(cards)-reconstructed,"unique_factor_count":len(factors),
            "family_count":len(families),"family_count_definition":"STRUCTURAL_ECONOMIC_SIGNATURE_NOT_INDEPENDENCE_PROOF",
            "compute_verified_count":0,"research_eligible_count":0,"released_count":0,"performance_inputs_used":False,
            "missing_field_counts":dict(Counter(n for c in cards for n in c["missing_fields"]))}}
