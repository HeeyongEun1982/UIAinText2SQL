"""Select and analyse the Chapter 3 RQ3 transition sample.

Selection uses only outcome pattern, database, difficulty, and question id.
Only after the 150-query manifest is frozen does the script read SQL and
clarification traces.  SQL comparisons are reproducible structural diagnostics;
they are not presented as causal labels.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import sqlglot
from sqlglot import exp


HERE = Path(__file__).resolve().parent
PAPER_DIR = HERE.parent
ROOT = PAPER_DIR.parents[2]
RUN_BASE = ROOT / "revised_final_bif_sif"
INPUT_DIR = HERE / "inputs"
OUTPUT_DIR = HERE / "outputs"

BIF_ROOTS = [
    RUN_BASE / "0.bif" / "runs" / "0.bif_full_1534_20260821_2201",
    RUN_BASE / "0.bif" / "runs" / "0.bif_full_1534_20260821_2201_",
]
SIF_ROOT = RUN_BASE / "1.sif" / "runs" / "1.sif_full_1534_20260831_215201"
COMBINED_ROOT = RUN_BASE / "2.bif_sif" / "runs" / "2.bif_sif_full_1534_20260823_012700"

SEED = 20_260_902
TARGET_PATTERNS = ("001", "010", "011", "100", "101", "110")
PER_PATTERN = 25
# Retained only to reproduce the superseded 150-case development sample.
# The canonical RQ3 census uses the combined-condition records for all 486
# outcome-changing queries and does not apply this SIF-only/RQ1 exclusion.
LEGACY_SAMPLE_EXCLUDED_QIDS = set(range(1, 11))
CONDITIONS = ("bif_only", "sif_only", "bif_sif")
LOCUS_ORDER = (
    "source_join",
    "filter_literal",
    "calculation_aggregation",
    "grouping_deduplication",
    "output_projection",
    "ordering_limit",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def selection_hash(row: dict[str, Any]) -> str:
    key = (
        f"RQ3-v0.3|{SEED}|{row['pattern']}|{row['difficulty']}|"
        f"{row['db_id']}|{row['question_id']}"
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def allocate(groups: dict[tuple[str, str], list[dict[str, Any]]], target: int) -> dict[tuple[str, str], int]:
    total = sum(len(rows) for rows in groups.values())
    if total < target:
        raise ValueError(f"Pattern has {total} eligible rows, fewer than target {target}")
    quotas = {key: target * len(rows) / total for key, rows in groups.items()}
    assigned = {key: min(len(groups[key]), math.floor(value)) for key, value in quotas.items()}
    remaining = target - sum(assigned.values())
    order = sorted(groups, key=lambda key: (-(quotas[key] - math.floor(quotas[key])), key))
    while remaining:
        progressed = False
        for key in order:
            if assigned[key] < len(groups[key]):
                assigned[key] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise RuntimeError("Unable to complete proportional allocation")
    return assigned


def load_official_long() -> pd.DataFrame:
    path = INPUT_DIR / "official_bird_ex_recomputed.csv"
    frame = pd.read_csv(path)
    required = {
        "condition",
        "question_id",
        "db_id",
        "difficulty",
        "official_ex",
        "predicted_sql",
        "gold_sql",
        "status",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Official EX input missing fields: {sorted(missing)}")
    if len(frame) != 1534 * 3:
        raise ValueError(f"Expected 4,602 long rows, found {len(frame)}")
    return frame


def make_wide(long: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for qid, group in long.groupby("question_id", sort=True):
        by_condition = {str(row.condition): row for row in group.itertuples(index=False)}
        if set(by_condition) != set(CONDITIONS):
            raise ValueError(f"qid {qid}: condition mismatch {sorted(by_condition)}")
        base = by_condition["bif_only"]
        pattern = "".join(str(int(by_condition[name].official_ex)) for name in CONDITIONS)
        rows.append(
            {
                "question_id": int(qid),
                "db_id": str(base.db_id),
                "difficulty": str(base.difficulty),
                "bif_only": int(by_condition["bif_only"].official_ex),
                "sif_only": int(by_condition["sif_only"].official_ex),
                "bif_sif": int(by_condition["bif_sif"].official_ex),
                "pattern": pattern,
            }
        )
    if [row["question_id"] for row in rows] != list(range(1534)):
        raise ValueError("Wide table is not the complete qid range 0--1533")
    return rows


def freeze_sample(wide: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    allocations: dict[str, Any] = {}
    for pattern in TARGET_PATTERNS:
        candidates = [
            row
            for row in wide
            if row["pattern"] == pattern and row["question_id"] not in LEGACY_SAMPLE_EXCLUDED_QIDS
        ]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            groups[(row["difficulty"], row["db_id"])].append(row)
        assigned = allocate(groups, PER_PATTERN)
        allocations[pattern] = {
            f"{difficulty}|{db_id}": count
            for (difficulty, db_id), count in sorted(assigned.items())
            if count
        }
        for key, count in sorted(assigned.items()):
            group_rows = sorted(groups[key], key=selection_hash)
            for row in group_rows[:count]:
                selected.append(
                    {
                        "pattern": pattern,
                        "question_id": row["question_id"],
                        "db_id": row["db_id"],
                        "difficulty": row["difficulty"],
                        "selection_hash": selection_hash(row),
                    }
                )
    selected.sort(key=lambda row: (row["pattern"], row["difficulty"], row["db_id"], row["selection_hash"]))
    counts = Counter(row["pattern"] for row in selected)
    if counts != Counter({pattern: PER_PATTERN for pattern in TARGET_PATTERNS}):
        raise ValueError(f"Unexpected sample counts: {counts}")
    manifest = {
        "seed": SEED,
        "per_pattern": PER_PATTERN,
        "patterns": list(TARGET_PATTERNS),
        "total": len(selected),
        "selection_variables": ["pattern", "difficulty", "db_id", "question_id"],
        "sql_or_clarification_used_during_selection": False,
        "allocation": "proportional by difficulty x database, largest remainder; SHA-256 order within stratum",
        "legacy_sample_excluded_qids": sorted(LEGACY_SAMPLE_EXCLUDED_QIDS),
        "stratum_allocations": allocations,
    }
    return selected, manifest


def outer_select(tree: exp.Expression | None) -> exp.Select | None:
    if tree is None:
        return None
    if isinstance(tree, exp.Select):
        return tree
    return tree.find(exp.Select)


def column_names(node: exp.Expression | None) -> list[str]:
    if node is None:
        return []
    return sorted(str(col.name).lower() for col in node.find_all(exp.Column))


def literal_values(node: exp.Expression | None) -> list[str]:
    if node is None:
        return []
    values = []
    for value in node.find_all(exp.Literal):
        raw = str(value.this)
        if value.is_string:
            values.append(f"string:{raw}")
            continue
        try:
            numeric = Decimal(raw).normalize()
            values.append(f"number:{numeric}")
        except InvalidOperation:
            values.append(f"literal:{raw}")
    values.extend("null" for _ in node.find_all(exp.Null))
    return sorted(values)


def operator_names(node: exp.Expression | None, classes: Iterable[type[exp.Expression]]) -> list[str]:
    if node is None:
        return []
    names: list[str] = []
    for cls in classes:
        names.extend(cls.__name__.lower() for _ in node.find_all(cls))
    return sorted(names)


def expression_signature(node: exp.Expression) -> dict[str, Any]:
    if isinstance(node, exp.Alias):
        node = node.this
    aggregates = sorted(type(item).__name__.lower() for item in node.find_all(exp.AggFunc))
    arithmetic = operator_names(node, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod))
    functions = sorted(
        type(item).__name__.lower()
        for item in node.find_all(exp.Func)
        if not isinstance(item, exp.AggFunc)
    )
    return {
        "columns": column_names(node),
        "aggregates": aggregates,
        "arithmetic": arithmetic,
        "functions": functions,
        "stars": sum(1 for _ in node.find_all(exp.Star)),
        "literal_count": sum(1 for _ in node.find_all(exp.Literal)),
    }


def table_aliases(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = str(table.name).lower()
        aliases[name] = name
        alias = str(table.alias or "").lower()
        if alias:
            aliases[alias] = name
    return aliases


def flatten_binary(node: exp.Expression, cls: type[exp.Expression]) -> list[exp.Expression]:
    if isinstance(node, cls):
        return flatten_binary(node.this, cls) + flatten_binary(node.expression, cls)
    return [node]


def canonical_expression(node: Any, aliases: dict[str, str]) -> str:
    """Return an alias-normalised, clause-sensitive structural representation."""

    if node is None:
        return ""
    if isinstance(node, list):
        return "[" + ",".join(canonical_expression(item, aliases) for item in node) + "]"
    if not isinstance(node, exp.Expression):
        return repr(node)
    if isinstance(node, exp.Alias):
        return canonical_expression(node.this, aliases)
    if isinstance(node, exp.Paren):
        return canonical_expression(node.this, aliases)
    if isinstance(node, exp.Column):
        qualifier = str(node.table or "").lower()
        qualifier = aliases.get(qualifier, qualifier)
        name = str(node.name).lower()
        return f"column:{qualifier + '.' if qualifier else ''}{name}"
    if isinstance(node, exp.Table):
        return f"table:{str(node.name).lower()}"
    if isinstance(node, exp.Identifier):
        return f"identifier:{str(node.this).lower()}"
    if isinstance(node, exp.Literal):
        raw = str(node.this)
        if node.is_string:
            return f"string:{raw}"
        try:
            return f"number:{Decimal(raw).normalize()}"
        except InvalidOperation:
            return f"literal:{raw}"
    if isinstance(node, exp.Null):
        return "null"
    if isinstance(node, (exp.And, exp.Or)):
        terms = sorted(
            canonical_expression(item, aliases)
            for item in flatten_binary(node, type(node))
        )
        return f"{type(node).__name__.lower()}({','.join(terms)})"
    if isinstance(node, exp.In):
        query = node.args.get("query")
        expressions = node.args.get("expressions") or []
        right = (
            canonical_expression(query, aliases)
            if query is not None
            else "[" + ",".join(
                sorted(canonical_expression(item, aliases) for item in expressions)
            ) + "]"
        )
        return f"in({canonical_expression(node.this, aliases)},{right})"
    if isinstance(node, (exp.EQ, exp.NEQ, exp.Add, exp.Mul)):
        terms = sorted(
            (
                canonical_expression(node.this, aliases),
                canonical_expression(node.expression, aliases),
            )
        )
        return f"{type(node).__name__.lower()}({','.join(terms)})"

    parts: list[str] = []
    for key, value in sorted(node.args.items()):
        if key in {"alias", "comments"} or value is None:
            continue
        if isinstance(value, list):
            rendered = "[" + ",".join(canonical_expression(item, aliases) for item in value) + "]"
        elif isinstance(value, exp.Expression):
            rendered = canonical_expression(value, aliases)
        elif isinstance(value, str):
            rendered = value.lower()
        else:
            rendered = repr(value)
        parts.append(f"{key}={rendered}")
    return f"{type(node).__name__.lower()}({';'.join(parts)})"


def detailed_locus_signatures(tree: exp.Expression) -> dict[str, str]:
    aliases = table_aliases(tree)
    joins = []
    for join in tree.find_all(exp.Join):
        kind = str(join.args.get("kind") or "").lower()
        if not kind:
            kind = "inner"
        joins.append(
            {
                "target": canonical_expression(join.this, aliases),
                "side": str(join.args.get("side") or "").lower(),
                "kind": kind,
                "method": str(join.args.get("method") or "").lower(),
                "on": canonical_expression(join.args.get("on"), aliases),
                "using": canonical_expression(join.args.get("using"), aliases),
            }
        )
    source_join = {
        "tables": sorted({str(table.name).lower() for table in tree.find_all(exp.Table)}),
        "joins": sorted(joins, key=lambda item: json.dumps(item, sort_keys=True)),
    }

    filter_literal = {
        "where": sorted(
            canonical_expression(item.this, aliases)
            for item in tree.find_all(exp.Where)
        ),
        "having": sorted(
            canonical_expression(item.this, aliases)
            for item in tree.find_all(exp.Having)
        ),
    }

    calculation_nodes: list[exp.Expression] = []
    calculation_types = (exp.AggFunc, exp.Window, exp.Case, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
    for item in tree.walk():
        if isinstance(item, calculation_types):
            calculation_nodes.append(item)
    calculation_aggregation = {
        "expressions": sorted(
            {canonical_expression(item, aliases) for item in calculation_nodes}
        ),
        "subquery_count": sum(1 for _ in tree.find_all(exp.Subquery)),
    }

    select = outer_select(tree)
    selects = list(tree.find_all(exp.Select))
    if isinstance(tree, exp.Select) and tree not in selects:
        selects.insert(0, tree)
    set_operations = sorted(
        type(item).__name__.lower()
        for item in tree.walk()
        if isinstance(item, (exp.Union, exp.Intersect, exp.Except))
    )
    grouping_deduplication = {
        "select_scopes": sorted(
            (
                {
                    "outer": scope is select,
                    "group": sorted(
                        canonical_expression(item, aliases)
                        for item in (
                            scope.args["group"].expressions
                            if isinstance(scope.args.get("group"), exp.Group)
                            else []
                        )
                    ),
                    "distinct": bool(scope.args.get("distinct")),
                }
                for scope in selects
            ),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        "set_operations": set_operations,
    }

    output_projection = {
        "expressions": [
            canonical_expression(item, aliases)
            for item in (select.expressions if select is not None else [])
        ]
    }

    ordering_limit = {
        "select_scopes": sorted(
            (
                {
                    "outer": scope is select,
                    "order": [
                        canonical_expression(item, aliases)
                        for item in (
                            scope.args["order"].expressions
                            if isinstance(scope.args.get("order"), exp.Order)
                            else []
                        )
                    ],
                    "limit": canonical_expression(scope.args.get("limit"), aliases),
                    "offset": canonical_expression(scope.args.get("offset"), aliases),
                }
                for scope in selects
            ),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        "root_order": canonical_expression(tree.args.get("order"), aliases),
        "root_limit": canonical_expression(tree.args.get("limit"), aliases),
        "root_offset": canonical_expression(tree.args.get("offset"), aliases),
    }

    values = {
        "source_join": source_join,
        "filter_literal": filter_literal,
        "calculation_aggregation": calculation_aggregation,
        "grouping_deduplication": grouping_deduplication,
        "output_projection": output_projection,
        "ordering_limit": ordering_limit,
    }
    return {
        f"{name}_signature": json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for name, value in values.items()
    }


def sql_features(sql: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "parse_ok": False,
        "parse_error": "",
        "tables": [],
        "table_count": 0,
        "join_count": 0,
        "join_columns": [],
        "filter_columns": [],
        "filter_literals": [],
        "filter_operators": [],
        "temporal_filter": False,
        "projection": [],
        "projection_count": 0,
        "aggregate_functions": [],
        "arithmetic_operators": [],
        "group_columns": [],
        "distinct": False,
        "order_columns": [],
        "order_directions": [],
        "limit": "",
        "subquery_count": 0,
        **{f"{name}_signature": "" for name in LOCUS_ORDER},
    }
    try:
        tree = sqlglot.parse_one(str(sql or ""), read="sqlite")
    except Exception as exc:  # preserve parse failures as data
        result["parse_error"] = f"{type(exc).__name__}: {exc}"
        return result
    if tree is None:
        result["parse_error"] = "empty parse tree"
        return result
    result["parse_ok"] = True
    result["tables"] = sorted({str(table.name).lower() for table in tree.find_all(exp.Table)})
    result["table_count"] = len(result["tables"])
    joins = list(tree.find_all(exp.Join))
    result["join_count"] = len(joins)
    join_columns: list[list[str]] = []
    for join in joins:
        names = column_names(join.args.get("on"))
        if names:
            join_columns.append(names)
    result["join_columns"] = sorted(join_columns)

    select = outer_select(tree)
    if select is not None:
        projections = [expression_signature(item) for item in select.expressions]
        result["projection"] = projections
        result["projection_count"] = len(projections)
        result["distinct"] = bool(select.args.get("distinct"))
        group = select.args.get("group")
        result["group_columns"] = column_names(group)
        order = select.args.get("order")
        if isinstance(order, exp.Order):
            result["order_columns"] = column_names(order)
            result["order_directions"] = [
                "desc" if bool(item.args.get("desc")) else "asc" for item in order.expressions
            ]
        limit = select.args.get("limit")
        if isinstance(limit, exp.Limit):
            expr_node = limit.args.get("expression")
            result["limit"] = str(getattr(expr_node, "this", expr_node) or "").lower()

    where = tree.find(exp.Where)
    having = tree.find(exp.Having)
    filters: exp.Expression | None = None
    if where is not None and having is not None:
        filters = exp.And(this=where.this, expression=having.this)
    elif where is not None:
        filters = where.this
    elif having is not None:
        filters = having.this
    result["filter_columns"] = column_names(filters)
    result["filter_literals"] = literal_values(filters)
    result["filter_operators"] = operator_names(
        filters,
        (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike, exp.In, exp.Is, exp.Between, exp.And, exp.Or),
    )
    result["temporal_filter"] = any(
        re.search(r"date|year|month|day|time|season|period|created|updated", name)
        for name in result["filter_columns"]
    )
    result["aggregate_functions"] = sorted(
        type(item).__name__.lower() for item in tree.find_all(exp.AggFunc)
    )
    result["arithmetic_operators"] = operator_names(
        tree, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
    )
    result["subquery_count"] = sum(1 for _ in tree.find_all(exp.Subquery))
    result.update(detailed_locus_signatures(tree))
    return result


def locus_differences(candidate: dict[str, Any], gold: dict[str, Any]) -> dict[str, int]:
    if not candidate["parse_ok"] or not gold["parse_ok"]:
        return {name: 1 for name in LOCUS_ORDER}
    return {
        name: int(candidate[f"{name}_signature"] != gold[f"{name}_signature"])
        for name in LOCUS_ORDER
    }


def turn_paths(root: Path, qid: int, pattern: str) -> list[Path]:
    return sorted((root / str(qid)).glob(pattern), key=lambda path: str(path))


def simplified_turn(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return {
        "turn": payload.get("turn"),
        "decision_context": payload.get("ambiguity") or payload.get("axis_key") or "",
        "question": payload.get("question_text") or payload.get("question") or "",
        "options": payload.get("options") or [],
        "selected_option": payload.get("selected_option_id") or payload.get("selected_option") or "",
        "answer": payload.get("answer_text") or "",
        "custom_correction": payload.get("custom_correction"),
        "source_path": str(path),
        "source_sha256": sha256(path),
    }


def collect_clarification(condition: str, qid: int) -> list[dict[str, Any]]:
    if condition == "bif_only":
        paths = turn_paths(
            BIF_ROOTS[0],
            qid,
            "P01/semantic_frontend/05_clarification/turn_*/turn_record.json",
        )
        if not paths:
            paths = turn_paths(
                BIF_ROOTS[1],
                qid,
                "P01/semantic_frontend/05_clarification/turn_*/turn_record.json",
            )
        return [simplified_turn(path) for path in paths]
    if condition == "sif_only":
        paths = turn_paths(
            SIF_ROOT, qid, "P01/frames/sif_clarification/turn_*/turn_record.json"
        )
        return [simplified_turn(path) for path in paths]

    turns = [
        simplified_turn(path)
        for path in turn_paths(
            COMBINED_ROOT,
            qid,
            "P01/semantic_frontend/05_clarification/turn_*/turn_record.json",
        )
    ]
    confirmed = COMBINED_ROOT / str(qid) / "P03" / "frames" / "confirmed_sif.json"
    if confirmed.is_file():
        payload = read_json(confirmed)
        for index, decision in enumerate(payload.get("active_inspection_decisions") or [], start=1):
            turns.append(
                {
                    "turn": index,
                    "decision_context": decision.get("axis_key") or "",
                    "question": decision.get("question") or "",
                    "options": [],
                    "selected_option": decision.get("selected_option") or "",
                    "answer": decision.get("answer_text") or "",
                    "custom_correction": decision.get("custom_correction"),
                    "source_path": str(confirmed),
                    "source_sha256": sha256(confirmed),
                }
            )
    return turns


TOPIC_PATTERNS = {
    "source_join": re.compile(r"\b(table|join|relationship|link|source|entity|role|owner|actor)\b", re.I),
    "filter_literal": re.compile(r"\b(filter|condition|include|exclude|scope|population|where|having|literal|code|value|any|all)\b", re.I),
    "calculation_aggregation": re.compile(r"\b(metric|calculate|calculation|average|sum|count|ratio|rate|percent|aggregate|denominator|total)\b", re.I),
    "grouping_deduplication": re.compile(r"\b(group|granularity|duplicate|distinct|per |each |one row|consolidat)\b", re.I),
    "output_projection": re.compile(r"\b(output|return|display|show|column|identifier|name|label|address|field)\b", re.I),
    "ordering_limit": re.compile(r"\b(order|sort|top|bottom|highest|lowest|first|last|tie|limit|rank)\b", re.I),
    "temporal": re.compile(r"\b(date|year|month|day|time|season|period|current|latest|earliest)\b", re.I),
}


def clarification_topics(turns: list[dict[str, Any]]) -> list[str]:
    text = " ".join(
        str(turn.get(field) or "")
        for turn in turns
        for field in ("decision_context", "question", "answer", "custom_correction")
    )
    return [name for name, regex in TOPIC_PATTERNS.items() if regex.search(text)]


def directional_labels(candidate: dict[str, Any], gold: dict[str, Any], status: str) -> list[str]:
    labels: list[str] = []
    if status != "ok":
        labels.append("execution_or_prediction_failure")
    if not candidate["parse_ok"]:
        labels.append("sql_parse_failure")
        return labels
    if candidate["table_count"] > gold["table_count"] or candidate["join_count"] > gold["join_count"]:
        labels.append("additional_source_or_join")
    if candidate["table_count"] < gold["table_count"] or candidate["join_count"] < gold["join_count"]:
        labels.append("missing_source_or_join")
    cand_pred = len(candidate["filter_operators"])
    gold_pred = len(gold["filter_operators"])
    if cand_pred > gold_pred:
        labels.append("additional_filter_constraint")
    elif cand_pred < gold_pred:
        labels.append("missing_filter_constraint")
    if candidate["filter_literals"] != gold["filter_literals"]:
        labels.append("literal_or_value_difference")
    if candidate["aggregate_functions"] != gold["aggregate_functions"] or candidate["arithmetic_operators"] != gold["arithmetic_operators"]:
        labels.append("calculation_or_aggregation_difference")
    if candidate["distinct"] != gold["distinct"] or candidate["group_columns"] != gold["group_columns"]:
        labels.append("grouping_or_deduplication_difference")
    if candidate["projection_count"] > gold["projection_count"]:
        labels.append("additional_output_projection")
    elif candidate["projection_count"] < gold["projection_count"]:
        labels.append("missing_output_projection")
    elif candidate["projection"] != gold["projection"]:
        labels.append("output_projection_choice_difference")
    if (
        candidate["order_columns"] != gold["order_columns"]
        or candidate["order_directions"] != gold["order_directions"]
        or candidate["limit"] != gold["limit"]
    ):
        labels.append("ordering_limit_or_tie_difference")
    return labels


def build_case_bundle(
    selected: list[dict[str, Any]], long: pd.DataFrame, queries: dict[int, dict[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_key = {
        (int(row.question_id), str(row.condition)): row
        for row in long.itertuples(index=False)
    }
    bundle: list[dict[str, Any]] = []
    coding: list[dict[str, Any]] = []
    for sample in selected:
        qid = int(sample["question_id"])
        query = queries[qid]
        gold_sql = str(by_key[(qid, "bif_only")].gold_sql or "")
        gold_features = sql_features(gold_sql)
        case: dict[str, Any] = {
            **sample,
            "question": query["question"],
            "evidence": query["evidence"],
            "ground_truth_sql": gold_sql,
            "ground_truth_features": gold_features,
            "conditions": {},
        }
        flat: dict[str, Any] = {
            **sample,
            "question": query["question"],
            "evidence": query["evidence"],
        }
        for condition in CONDITIONS:
            source = by_key[(qid, condition)]
            sql = str(source.predicted_sql or "")
            features = sql_features(sql)
            differences = locus_differences(features, gold_features)
            clarification = collect_clarification(condition, qid)
            topics = clarification_topics(clarification)
            labels = directional_labels(features, gold_features, str(source.status))
            case["conditions"][condition] = {
                "official_ex": int(source.official_ex),
                "status": str(source.status),
                "predicted_sql": sql,
                "features": features,
                "difference_loci": differences,
                "directional_difference_labels": labels,
                "clarification_topics": topics,
                "clarification": clarification,
            }
            flat[f"{condition}_official_ex"] = int(source.official_ex)
            flat[f"{condition}_status"] = str(source.status)
            flat[f"{condition}_sql"] = sql
            flat[f"{condition}_difference_loci"] = "|".join(
                name for name in LOCUS_ORDER if differences[name]
            )
            flat[f"{condition}_directional_labels"] = "|".join(labels)
            flat[f"{condition}_clarification_topics"] = "|".join(topics)
            flat[f"{condition}_clarification_turns"] = len(clarification)

        combined = case["conditions"]["bif_sif"]
        combined_diff = {
            name for name, flag in combined["difference_loci"].items() if flag
        }
        combined_topics = set(combined["clarification_topics"])
        flat["combined_clarification_difference_overlap"] = "|".join(
            sorted(combined_diff & combined_topics)
        )
        failed_singles = [
            name
            for name in ("bif_only", "sif_only")
            if case["conditions"][name]["official_ex"] == 0
        ]
        aligned_loci: list[str] = []
        for locus in LOCUS_ORDER:
            if not combined["difference_loci"][locus] and any(
                case["conditions"][name]["difference_loci"][locus]
                for name in failed_singles
            ):
                aligned_loci.append(locus)
        flat["combined_alignment_loci_vs_failed_singles"] = "|".join(aligned_loci)
        bundle.append(case)
        coding.append(flat)
    return bundle, coding


def load_queries() -> dict[int, dict[str, str]]:
    with (INPUT_DIR / "bird_dev_queries.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {int(row["question_id"]): row for row in rows}


def sample_locus_summary(coding: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for pattern in TARGET_PATTERNS:
        subset = [row for row in coding if row["pattern"] == pattern]
        for condition in CONDITIONS:
            for locus in LOCUS_ORDER:
                count = sum(
                    locus in str(row[f"{condition}_difference_loci"]).split("|")
                    for row in subset
                )
                summary.append(
                    {
                        "pattern": pattern,
                        "condition": condition,
                        "locus": locus,
                        "sample_n": len(subset),
                        "difference_count": count,
                        "difference_percent": round(100 * count / len(subset), 2),
                    }
                )
        label_counter: Counter[str] = Counter()
        for row in subset:
            label_counter.update(
                value for value in str(row["bif_sif_directional_labels"]).split("|") if value
            )
        for label, count in sorted(label_counter.items()):
            summary.append(
                {
                    "pattern": pattern,
                    "condition": "bif_sif_directional",
                    "locus": label,
                    "sample_n": len(subset),
                    "difference_count": count,
                    "difference_percent": round(100 * count / len(subset), 2),
                }
            )
    return summary


def schema_sizes(queries: dict[int, dict[str, str]]) -> dict[str, dict[str, int]]:
    first_qid: dict[str, int] = {}
    for qid, row in queries.items():
        first_qid.setdefault(row["db_id"], qid)
    sizes: dict[str, dict[str, int]] = {}
    for db_id, qid in first_qid.items():
        path = BIF_ROOTS[0] / str(qid) / "P00" / "inputs" / "schema_snapshot.json"
        payload = read_json(path)
        tables = payload.get("tables") or []
        sizes[db_id] = {
            "schema_table_count": len(tables),
            "schema_column_count": sum(len(table.get("columns") or []) for table in tables),
        }
    return sizes


def boundary_outputs(
    wide: list[dict[str, Any]], long: pd.DataFrame, queries: dict[int, dict[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    gold_by_qid = (
        long[long["condition"] == "bif_only"]
        .set_index("question_id")["gold_sql"]
        .to_dict()
    )
    sizes = schema_sizes(queries)
    feature_rows: list[dict[str, Any]] = []
    for row in wide:
        qid = row["question_id"]
        features = sql_features(str(gold_by_qid[qid] or ""))
        size = sizes[row["db_id"]]
        feature_rows.append(
            {
                **row,
                "gold_join_count": features["join_count"],
                "gold_table_count": features["table_count"],
                **size,
                "combined_gain_any": int(row["pattern"] in {"001", "011", "101"}),
                "combined_regression_any": int(row["pattern"] in {"010", "100", "110"}),
                "strict_rescue": int(row["pattern"] == "001"),
                "strict_regression": int(row["pattern"] == "110"),
            }
        )

    def aggregate(name: str, key_fn) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in feature_rows:
            groups[str(key_fn(row))].append(row)
        out: list[dict[str, Any]] = []
        for group, rows in sorted(groups.items()):
            n = len(rows)
            out.append(
                {
                    "stratifier": name,
                    "group": group,
                    "n": n,
                    "bif_only_ex": round(sum(row["bif_only"] for row in rows) / n, 6),
                    "sif_only_ex": round(sum(row["sif_only"] for row in rows) / n, 6),
                    "bif_sif_ex": round(sum(row["bif_sif"] for row in rows) / n, 6),
                    "combined_gain_any_n": sum(row["combined_gain_any"] for row in rows),
                    "combined_gain_any_rate": round(sum(row["combined_gain_any"] for row in rows) / n, 6),
                    "combined_regression_any_n": sum(row["combined_regression_any"] for row in rows),
                    "combined_regression_any_rate": round(sum(row["combined_regression_any"] for row in rows) / n, 6),
                    "strict_rescue_n": sum(row["strict_rescue"] for row in rows),
                    "strict_regression_n": sum(row["strict_regression"] for row in rows),
                }
            )
        return out

    outputs = {
        "difficulty": aggregate("difficulty", lambda row: row["difficulty"]),
        "database": aggregate("database", lambda row: row["db_id"]),
        "gold_join_count": aggregate(
            "gold_join_count",
            lambda row: "0" if row["gold_join_count"] == 0 else ("1" if row["gold_join_count"] == 1 else "2+"),
        ),
        "gold_table_count": aggregate(
            "gold_table_count",
            lambda row: "1" if row["gold_table_count"] <= 1 else ("2" if row["gold_table_count"] == 2 else "3+"),
        ),
        "schema_size_by_database": [],
    }
    for row in outputs["database"]:
        size = sizes[row["group"]]
        outputs["schema_size_by_database"].append({**row, **size})
    return feature_rows, outputs


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    long = load_official_long()
    queries = load_queries()
    wide = make_wide(long)
    write_csv(INPUT_DIR / "official_bird_ex_paired_wide.csv", wide)

    selected, selection_manifest = freeze_sample(wide)
    write_csv(OUTPUT_DIR / "rq3_sample_manifest.csv", selected)
    write_json(OUTPUT_DIR / "rq3_sample_manifest.json", selection_manifest)

    bundle, coding = build_case_bundle(selected, long, queries)
    bundle_path = INPUT_DIR / "rq3_sample_case_bundle.jsonl"
    with bundle_path.open("w", encoding="utf-8") as handle:
        for case in bundle:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    write_csv(OUTPUT_DIR / "rq3_sample_structural_coding.csv", coding)
    write_csv(OUTPUT_DIR / "rq3_sample_locus_summary.csv", sample_locus_summary(coding))

    feature_rows, boundary = boundary_outputs(wide, long, queries)
    write_csv(OUTPUT_DIR / "gold_sql_boundary_features.csv", feature_rows)
    for name, rows in boundary.items():
        write_csv(OUTPUT_DIR / f"boundary_{name}.csv", rows)

    manifest = {
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256(Path(__file__).resolve()),
        "official_input_sha256": sha256(INPUT_DIR / "official_bird_ex_recomputed.csv"),
        "query_input_sha256": sha256(INPUT_DIR / "bird_dev_queries.csv"),
        "sample_manifest_sha256": sha256(OUTPUT_DIR / "rq3_sample_manifest.csv"),
        "case_bundle_sha256": sha256(bundle_path),
        "sample_counts": dict(Counter(row["pattern"] for row in selected)),
        "sql_parser": f"sqlglot {sqlglot.__version__}",
        "interpretation": "Structural locus flags are reproducible diagnostics, not causal failure labels.",
    }
    write_json(OUTPUT_DIR / "rq3_trace_analysis_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
