"""Create the language-model-assisted full-content review of 375 SIF turns.

The reviewed labels below were assigned after reading each clarification
question, all offered options, and its decision-context record.  Existing
rule/final labels are retained only as the ``initial_label`` comparison
column and are not used to select ``reviewed_label``.
"""

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "inputs" / "rq1_turns_blinded.csv"
PRIOR = ROOT / "outputs" / "rq1_turn_labels_final.csv"
OUTPUT = ROOT / "outputs" / "rq1_sif_full_content_review.csv"


# One full-content-reviewed label per row, in the preserved input order.
# Blocks contain 25 rows each (last index: 374).
LABEL_BLOCKS = [
    "SCOPE METRIC OUTPUT SCOPE SCOPE SCOPE OUTPUT OUTPUT OUTPUT OUTPUT SCOPE GAP SCOPE SCOPE GAP OUTPUT REL OUTPUT OUTPUT ATTRIBUTE OUTPUT OUTPUT SCOPE VALUE VALUE",
    "SCOPE ATTRIBUTE SCOPE SCOPE SCOPE VALUE OUTPUT SCOPE OUTPUT OUTPUT SCOPE REL OUTPUT SCOPE REL ATTRIBUTE TEMP GAP REL GAP ATTRIBUTE REL GAP OUTPUT REL",
    "METRIC REL SCOPE METRIC REL OUTPUT OUTPUT GAP ATTRIBUTE REL REL OUTPUT OUTPUT ATTRIBUTE ATTRIBUTE REL ATTRIBUTE REL SCOPE ATTRIBUTE METRIC ATTRIBUTE ATTRIBUTE ATTRIBUTE METRIC",
    "VALUE ATTRIBUTE RANK GAP GAP OUTPUT METRIC VALUE SCOPE REL SCOPE REL OUTPUT REL VALUE REL REL METRIC ATTRIBUTE OUTPUT REL OUTPUT REL METRIC OUTPUT",
    "REL OUTPUT REL TEMP ATTRIBUTE METRIC OUTPUT OUTPUT SCOPE REL REL SCOPE OUTPUT OUTPUT REL OUTPUT OUTPUT OUTPUT OUTPUT OUTPUT OUTPUT SCOPE OUTPUT OUTPUT OUTPUT",
    "GAP REL SCOPE REL GAP OUTPUT REL OUTPUT OUTPUT REL METRIC OUTPUT OUTPUT OUTPUT ATTRIBUTE REL METRIC SCOPE REL REL REL REL ATTRIBUTE REL GAP",
    "REL OUTPUT OUTPUT TEMP REL OUTPUT OUTPUT REL REL REL REL REL OUTPUT OUTPUT SCOPE TEMP METRIC TEMP RANK OUTPUT METRIC REL RANK OUTPUT GAP",
    "ATTRIBUTE GAP OUTPUT SCOPE SCOPE RANK METRIC RANK METRIC METRIC METRIC SCOPE METRIC METRIC RANK METRIC OUTPUT GAP METRIC METRIC METRIC RANK RANK METRIC METRIC",
    "METRIC METRIC ATTRIBUTE METRIC METRIC METRIC METRIC METRIC METRIC RANK METRIC OUTPUT METRIC OUTPUT METRIC METRIC METRIC METRIC METRIC METRIC OUTPUT OUTPUT REL TEMP REL",
    "REL REL REL REL TEMP REL REL TEMP REL REL REL REL REL REL REL REL REL REL REL REL REL REL REL REL TEMP",
    "REL REL TEMP REL REL REL REL REL REL REL REL REL REL REL GAP GAP GAP REL REL GAP GAP GAP REL GAP VALUE",
    "OUTPUT METRIC REL REL REL TEMP SCOPE OUTPUT OUTPUT OUTPUT REL OUTPUT METRIC SCOPE TEMP SCOPE TEMP SCOPE SCOPE VALUE SCOPE SCOPE SCOPE OUTPUT SCOPE",
    "SCOPE SCOPE OUTPUT SCOPE METRIC OUTPUT OUTPUT SCOPE REL SCOPE OUTPUT SCOPE VALUE OUTPUT METRIC OUTPUT OUTPUT SCOPE TEMP OUTPUT SCOPE TEMP RANK OUTPUT SCOPE",
    "SCOPE SCOPE GAP GAP GAP GAP GAP GAP GAP GAP SCOPE GAP GAP METRIC GAP METRIC GAP OUTPUT METRIC GAP METRIC GAP GAP ATTRIBUTE OUTPUT",
    "OUTPUT GAP GAP SCOPE METRIC GAP OUTPUT METRIC SCOPE ATTRIBUTE METRIC METRIC METRIC METRIC METRIC METRIC METRIC SCOPE METRIC GAP GAP METRIC VALUE METRIC METRIC",
]


PRE_ATTRIBUTE_OUTPUT_AUDIT_LABELS = " ".join(LABEL_BLOCKS).split()

# Second-pass ATTRIBUTE/OUTPUT boundary audit (98 turns total).
# Consistent rule from PDF v0.3 Section 3.6.1:
#   * OUTPUT: the option changes a column/identifier/text that the query asks
#     to return in the final SELECT, including a grouping key returned as the
#     answer (e.g., "which segment", "what race number").
#   * ATTRIBUTE: competing source columns are used only to identify/filter,
#     group, or calculate the target and are not themselves the requested
#     returned content.
# Every other label is unchanged by this audit.  The listed nine rows moved
# from ATTRIBUTE to OUTPUT; q584 and q851 were already changed relative to
# the earlier rule labels, while the other seven are newly corrected here.
ATTRIBUTE_OUTPUT_BOUNDARY_OVERRIDES = {
    19: "OUTPUT",   # q42: requested education type is returned
    66: "OUTPUT",   # q176: payment status/amount/history is returned
    93: "OUTPUT",   # q364: requested card status is returned
    139: "OUTPUT",  # q584: public comment text vs edit-summary text returned
    147: "OUTPUT",  # q637: requested tags are returned
    175: "OUTPUT",  # q851 turn 2: requested position is returned
    202: "OUTPUT",  # q979: winning race number/identifier is returned
    348: "OUTPUT",  # q1404: requested expense type is returned/grouped
    359: "OUTPUT",  # q1478: winning segment is returned/grouped
}

LABELS = PRE_ATTRIBUTE_OUTPUT_AUDIT_LABELS.copy()
for row_index, reviewed_label in ATTRIBUTE_OUTPUT_BOUNDARY_OVERRIDES.items():
    LABELS[row_index] = reviewed_label
ALLOWED = {"GAP", "REL", "TEMP", "METRIC", "SCOPE", "RANK", "VALUE", "OUTPUT", "ATTRIBUTE"}


RATIONALE = {
    "GAP": "요청을 직접 나타내는 schema 요소가 없어 proxy, 대체 표현 또는 미지원 처리를 정하는 질문이다.",
    "REL": "table 간 연결, join key/path 또는 연결된 entity의 역할 선택이 선택지 간 핵심 차이이다.",
    "TEMP": "사용할 date field, 시점 또는 여러 historical record 중 어느 version을 택할지가 핵심이다.",
    "METRIC": "stored 값과 재계산 값, 집계 함수, 분모 또는 counting grain의 선택이 결과 계산을 바꾼다.",
    "SCOPE": "질문의 대상이나 조건을 어느 schema field와 record 집합에 적용할지가 선택지마다 달라진다.",
    "RANK": "정렬 기준, earliest/latest, 상위 결과 또는 tie 처리 방식의 선택이 핵심이다.",
    "VALUE": "literal, code, 단위 또는 text 값을 database 값으로 해석ㆍ변환하는 방식의 선택이다.",
    "OUTPUT": "동일 요청에서 최종 반환할 column, identifier 또는 표시 형식을 고르는 질문이다.",
    "ATTRIBUTE": "같은 개념을 나타낼 수 있는 여러 table/column 가운데 사용할 source를 고르는 질문이다.",
}


DECISION_INTERPRETATION = {
    "GAP": "직접 대응하는 schema 요소가 없는 상태에서 proxyㆍ대체 표현ㆍ미지원 처리를 고르는 결정",
    "REL": "join key/path 또는 연결된 entity의 역할을 고르는 결정",
    "TEMP": "date field, 시점 또는 historical record version을 고르는 결정",
    "METRIC": "stored/derived 값, 집계식, 분모 또는 counting grain을 고르는 결정",
    "SCOPE": "filter가 적용될 schema field 또는 포함할 record 집합을 고르는 결정",
    "RANK": "정렬 기준, earliest/latest, 상위 결과 또는 tie 처리 방식을 고르는 결정",
    "VALUE": "literal, code, 단위 또는 text parsing 방식을 고르는 결정",
    "OUTPUT": "최종 반환 column, identifier 또는 표시 내용을 고르는 결정",
    "ATTRIBUTE": "같은 개념을 나타내는 경쟁 source table/column을 고르는 결정",
}


def compact(text: object, limit: int = 145) -> str:
    value = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def option_contrast(options_json: object) -> str:
    """Return a row-specific audit sentence using the actual offered options."""
    try:
        options = json.loads(str(options_json))
    except (TypeError, ValueError, json.JSONDecodeError):
        return "선택지 JSON을 파싱할 수 없어 전체 options_text를 확인해야 한다."

    rendered = []
    for option in options[:3]:
        option_id = str(option.get("option_id", "?")).strip() or "?"
        description = compact(option.get("description", ""))
        rendered.append(f'{option_id}="{description}"')
    if not rendered:
        return "보존된 선택지가 없다."
    suffix = f" 등 총 {len(options)}개" if len(options) > 3 else ""
    return "선택지 차이: " + "; ".join(rendered) + suffix + "."


def make_rationale(row: pd.Series, label: str) -> str:
    attribute = str(row.get("attribute_name", "")).strip()
    if not attribute or attribute.lower() == "nan":
        subject = "이 turn"
    else:
        subject = f"'{attribute}'"
    candidates = compact(row.get("candidate_columns", ""), limit=180)
    if not candidates or candidates.lower() == "nan":
        candidate_note = "직접 후보 column이 기록되지 않았다."
    else:
        candidate_note = f"기록된 후보는 {candidates}이다."
    contrast = option_contrast(row.get("options_json"))
    return (
        f"{contrast} {candidate_note} 따라서 {subject}에 관한 핵심 delta를 "
        f"{DECISION_INTERPRETATION[label]}으로 판단하여 {label}로 분류했다."
    )


def main() -> None:
    turns = pd.read_csv(INPUT)
    turns = turns.loc[
        (turns["architecture"] == "sif_only") & (turns["rq1_included"] == 1)
    ].copy()
    turns.reset_index(drop=True, inplace=True)

    prior = pd.read_csv(PRIOR)
    prior = prior.loc[
        (prior["architecture"] == "sif_only") & (prior["rq1_included"] == 1),
        ["question_id", "turn", "final_native_label"],
    ].copy()

    assert len(turns) == 375, f"expected 375 included SIF turns, found {len(turns)}"
    assert len(LABELS) == 375, f"expected 375 reviewed labels, found {len(LABELS)}"
    assert len(PRE_ATTRIBUTE_OUTPUT_AUDIT_LABELS) == 375
    assert set(LABELS) <= ALLOWED
    assert not turns.duplicated(["question_id", "turn"]).any()

    reviewed = turns.merge(
        prior,
        on=["question_id", "turn"],
        how="left",
        validate="one_to_one",
    )
    assert reviewed["final_native_label"].notna().all()

    reviewed.rename(
        columns={
            "question_id": "qid",
            "turn": "turn_index",
            "final_native_label": "initial_label",
        },
        inplace=True,
    )
    reviewed["reviewed_label"] = LABELS
    reviewed["pre_attribute_output_audit_label"] = PRE_ATTRIBUTE_OUTPUT_AUDIT_LABELS
    reviewed["attribute_output_boundary_reviewed"] = [
        (before in {"ATTRIBUTE", "OUTPUT"}) or (after in {"ATTRIBUTE", "OUTPUT"})
        for before, after in zip(PRE_ATTRIBUTE_OUTPUT_AUDIT_LABELS, LABELS)
    ]
    reviewed["attribute_output_boundary_changed"] = [
        before != after
        for before, after in zip(PRE_ATTRIBUTE_OUTPUT_AUDIT_LABELS, LABELS)
    ]
    reviewed["rationale"] = [
        make_rationale(row, label)
        for (_, row), label in zip(reviewed.iterrows(), LABELS)
    ]
    reviewed["changed"] = reviewed["initial_label"] != reviewed["reviewed_label"]

    reviewed = reviewed[
        [
            "qid",
            "turn_index",
            "db_id",
            "difficulty",
            "original_question",
            "evidence",
            "decision_context",
            "attribute_name",
            "candidate_columns",
            "clarification_question",
            "options_text",
            "initial_label",
            "pre_attribute_output_audit_label",
            "reviewed_label",
            "rationale",
            "changed",
            "attribute_output_boundary_reviewed",
            "attribute_output_boundary_changed",
            "source_path",
            "source_sha256",
        ]
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    reviewed.to_csv(OUTPUT, index=False, encoding="utf-8-sig")

    counts = reviewed["reviewed_label"].value_counts().sort_index()
    changes = int(reviewed["changed"].sum())
    required_audit_columns = [
        "original_question",
        "decision_context",
        "clarification_question",
        "options_text",
        "initial_label",
        "reviewed_label",
        "rationale",
    ]
    missing = reviewed[required_audit_columns].isna().sum()
    assert int(missing.sum()) == 0, f"missing required audit content:\n{missing}"
    assert not reviewed["qid"].between(1, 10).any()
    assert reviewed.loc[reviewed["changed"], "rationale"].str.startswith(
        "선택지 차이:"
    ).all()
    assert int(reviewed["attribute_output_boundary_reviewed"].sum()) == 98
    assert int(reviewed["attribute_output_boundary_changed"].sum()) == 9
    assert set(
        reviewed.index[reviewed["attribute_output_boundary_changed"]].tolist()
    ) == set(ATTRIBUTE_OUTPUT_BOUNDARY_OVERRIDES)
    print(f"wrote {OUTPUT}")
    print(f"rows={len(reviewed)} changes={changes}")
    print(
        "attribute_output_boundary="
        f"{int(reviewed['attribute_output_boundary_reviewed'].sum())} "
        "boundary_changes="
        f"{int(reviewed['attribute_output_boundary_changed'].sum())}"
    )
    print(f"required_audit_missing={int(missing.sum())}")
    print(counts.to_string())


if __name__ == "__main__":
    main()
