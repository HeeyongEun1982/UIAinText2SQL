"""Write the language-model-assisted full-content review of 432 BIF turns.

The labels below were assigned after reading each turn's decision context,
clarification question, and complete option set against the eight definitions
in Chapter 3 of the v0.3 methodology.  They are intentionally stored apart
from the rule classifier and final label file.  Existing labels are read only
after the content-review decisions have been fixed, solely to report changes.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
INPUT = HERE / "outputs" / "rq1_turn_labels_final.csv"
OUTPUT = HERE / "outputs" / "rq1_bif_full_content_review.csv"


# Tuple position is the one-based turn index.  The script verifies that every
# qid has exactly the expected consecutive turns before applying these labels.
REVIEWED_BY_QID: dict[int, tuple[str, ...]] = {
    8: ("RS", "TM", "RS"),
    17: ("PL",),
    19: ("RS", "SC", "PL"),
    21: ("RG", "RG"),
    23: ("MC",),
    24: ("PL", "PL"),
    25: ("MC", "RG"),
    26: ("RG", "UG", "TM", "RG"),
    28: ("RG",),
    35: ("RS",),
    37: ("RS",),
    38: ("RS", "RG", "TM", "OR"),
    42: ("RS", "TM", "MC"),
    43: ("RS", "MC"),
    44: ("SC", "RS"),
    58: ("RS",),
    67: ("RG", "RG", "RG"),
    83: ("SC",),
    84: ("RG", "RS", "RS"),
    87: ("PL", "RG", "RG", "RG"),
    88: ("RG", "RS", "TM"),
    94: ("SC", "PL", "RS"),
    99: ("PL",),
    104: ("RS",),
    108: ("RG", "RS", "RG", "SC", "PL"),
    110: ("RS",),
    112: ("RS",),
    116: ("RS",),
    119: ("TM", "TM"),
    120: ("TM", "RG", "OR"),
    125: ("TM", "UG", "UG"),
    129: ("RS",),
    131: ("RS", "RG", "RS"),
    134: ("RS", "TM"),
    144: ("UG", "MC"),
    145: ("UG",),
    156: ("RS", "RS"),
    169: ("UG",),
    170: ("PL",),
    173: ("MC", "MC", "TM"),
    176: ("RG", "RG"),
    181: ("RS", "SC"),
    186: ("MC",),
    189: ("PL", "SC", "RS"),
    198: ("MC", "MC"),
    207: ("OR", "OR"),
    219: ("UG",),
    220: ("RG", "RS", "RS"),
    233: ("RS", "RG", "RS"),
    243: ("PL", "PL"),
    253: ("UG", "UG"),
    254: ("UG", "RS", "UG", "SC"),
    259: ("UG", "PL"),
    260: ("SC", "UG", "UG"),
    273: ("MC", "UG", "MC", "MC"),
    284: ("RG", "RS", "OR"),
    293: ("UG", "UG"),
    295: ("UG", "PL"),
    297: ("RG", "RG"),
    305: ("RG", "OR"),
    308: ("RG", "SC", "MC"),
    317: ("MC",),
    324: ("UG", "MC", "MC"),
    325: ("PL",),
    329: ("RS", "OR"),
    334: ("RG", "RS"),
    339: ("OR", "RS"),
    347: ("RG",),
    354: ("RG", "SC", "SC"),
    362: ("RG", "RG"),
    390: ("RG",),
    417: ("MC",),
    420: ("PL",),
    421: ("RS",),
    426: ("RS",),
    430: ("RS", "RG", "RS"),
    442: ("PL",),
    446: ("MC",),
    448: ("RG", "RS", "RG"),
    474: ("PL",),
    495: ("RS",),
    503: ("RS",),
    510: ("RS",),
    515: ("RS",),
    558: ("RG", "TM", "SC"),
    576: ("PL",),
    584: ("RG", "SC"),
    587: ("UG",),
    595: ("PL", "PL"),
    600: ("RG",),
    612: ("RS", "RS"),
    616: ("RS", "OR", "RS"),
    618: ("RG", "UG"),
    642: ("TM",),
    648: ("RG",),
    649: ("RG",),
    650: ("RG", "RG"),
    654: ("RS", "RG"),
    656: ("RS",),
    670: ("RS",),
    673: ("MC", "MC"),
    683: ("MC", "TM"),
    685: ("RG",),
    687: ("RS",),
    692: ("RS", "RS", "OR"),
    701: ("RS",),
    713: ("RS", "RG"),
    715: ("RG", "UG"),
    716: ("UG", "UG", "UG"),
    720: ("MC",),
    737: ("RG", "RG"),
    759: ("RS",),
    763: ("OR", "OR"),
    767: ("MC", "UG"),
    788: ("MC",),
    801: ("RG",),
    810: ("RS", "RG"),
    840: ("OR",),
    851: ("RG", "RG"),
    858: ("RS", "RS"),
    871: ("RG",),
    880: ("MC", "RG"),
    883: ("RG", "RS"),
    888: ("RG",),
    890: ("RG",),
    891: ("RG", "SC"),
    895: ("SC", "SC"),
    897: ("MC",),
    917: ("RG",),
    920: ("RG", "SC"),
    922: ("TM", "TM", "OR", "UG"),
    942: ("SC", "SC"),
    944: ("MC",),
    945: ("RG", "RG"),
    949: ("RG", "TM", "RS"),
    951: ("PL", "PL", "MC"),
    962: ("PL", "TM"),
    963: ("MC", "UG"),
    972: ("SC", "RG", "RS"),
    976: ("RS", "SC", "RS"),
    981: ("RS", "RS"),
    983: ("RG", "TM"),
    988: ("PL", "PL"),
    996: ("TM",),
    1010: ("RG", "SC", "OR"),
    1011: ("RS", "RS", "TM"),
    1012: ("RG", "SC", "TM"),
    1013: ("RG", "RG", "SC"),
    1016: ("TM", "RG", "MC", "RS"),
    1022: ("RS",),
    1026: ("RS",),
    1038: ("RS", "OR"),
    1054: ("RG", "RG", "RG"),
    1058: ("RS", "OR"),
    1060: ("PL",),
    1062: ("PL", "PL"),
    1075: ("PL",),
    1108: ("TM",),
    1132: ("RS", "RG", "RS", "PL"),
    1160: ("MC", "PL"),
    1166: ("RS", "RS", "UG"),
    1168: ("RS", "RS"),
    1171: ("TM", "TM", "UG"),
    1185: ("MC", "MC"),
    1189: ("MC", "SC", "PL"),
    1192: ("TM",),
    1200: ("MC", "MC"),
    1205: ("PL",),
    1239: ("PL", "PL"),
    1241: ("PL",),
    1245: ("RG", "RG"),
    1253: ("RS", "RS", "RS"),
    1290: ("RS", "UG"),
    1292: ("PL", "PL"),
    1294: ("PL", "RS"),
    1296: ("RS",),
    1316: ("RS", "UG"),
    1322: ("UG", "SC"),
    1329: ("RG",),
    1330: ("RS", "RS"),
    1336: ("RG", "RG"),
    1339: ("MC", "UG", "RG"),
    1353: ("RS", "RG"),
    1385: ("PL",),
    1421: ("MC",),
    1430: ("RG",),
    1434: ("RG", "RG", "RG"),
    1436: ("MC", "RG", "TM"),
    1437: ("SC",),
    1440: ("MC", "UG"),
    1450: ("PL", "RG", "UG"),
    1456: ("RS",),
    1457: ("PL", "UG"),
    1467: ("UG",),
    1477: ("TM", "RS", "SC"),
    1478: ("RG", "RG", "TM", "MC", "RS"),
    1488: ("RG", "TM", "RG", "RS"),
    1507: ("RG", "UG"),
    1508: ("RG", "SC"),
    1511: ("UG", "PL", "RG"),
    1513: ("TM", "RG", "RS", "RG"),
    1515: ("RG", "TM", "TM"),
    1517: ("TM", "RS"),
    1520: ("RS", "MC"),
    1522: ("MC", "RS", "RG"),
    1524: ("MC", "RS", "RG"),
    1525: ("MC", "RG", "PL"),
    1528: ("RG", "MC", "TM", "MC"),
    1530: ("RS", "TM", "SC", "RS"),
    1531: ("MC", "MC", "MC", "TM"),
    1532: ("RG", "RS"),
    1533: ("PL", "RG"),
}


KOREAN_BOUNDARY = {
    "RG": "지칭 대상·용어·속성·역할의 의미",
    "RS": "전체/단일 결과 또는 결과 선택 기준",
    "MC": "계산식·집계 방식·분모",
    "SC": "계산이나 검색에 포함할 대상 집합",
    "PL": "AND/OR, any/all, 비교 경계 또는 필터 적용 위치",
    "TM": "연도·기간·날짜 기준 또는 보고 시점",
    "UG": "행·개체·distinct 개체 또는 그룹 계산 단위",
    "OR": "반환 속성·단위 또는 표시 형식",
}


# Content-specific explanations for rows whose category boundary was revisited
# after the full review.  These notes document the decisive difference between
# the complete option sets rather than merely repeating a category definition.
RATIONALE_OVERRIDE: dict[tuple[int, int], str] = {
    (67, 1): "금액의 합계, 폐쇄 사건·캠퍼스의 개수, 기타 양 중 'total amount'가 지칭하는 측정 개념 자체를 결정하므로 RG로 판정했다. SUM과 COUNT는 각 해석을 구현하는 연산이지, 같은 값을 계산하는 대안 공식이 아니다.",
    (19, 3): "선택지가 표본 수와 결측률 임계치를 적용할지, 그리고 어떤 조건으로 적용할지를 바꾸므로 PL로 판정했다.",
    (119, 1): "'accounts in 1993'을 계좌 개설일·거래일·명세서 발행일 중 어느 날짜 필드로 판단할지 결정하므로 TM으로 판정했다.",
    (233, 3): "본드 이름·상대 분자 이름·본드 유형 중 어느 필드를 알파벳 정렬 기준으로 삼을지 결정하므로 RS로 판정했다.",
    (254, 3): "element pair만으로 묶을지, bond order와 aromaticity까지 포함한 복합 key로 묶을지에 따라 서로 같은 combination으로 세는 단위가 달라지므로 UG로 판정했다.",
    (308, 1): "chlorine이 포함된 bond의 전체 개수, Cl atom당 개수 통계, chlorine의 화학적 valence 중 'how many bonds'가 어느 측정 개념을 뜻하는지 결정하므로 RG로 판정했다. 단순히 같은 값의 계산 공식이나 단위만 바꾸는 질문이 아니다.",
    (430, 3): "세트 이름과 세트 ID 중 어느 필드를 결과 정렬 기준으로 삼을지 결정하므로 RS로 판정했다.",
    (883, 2): "경주 이름·날짜/시간·참가자 수 중 어느 필드를 내림차순 정렬 기준으로 삼을지 결정하므로 RS로 판정했다.",
    (949, 3): "시즌 누적·단일 경주·역대 누적 점수 중 어느 값으로 최고 점수의 constructor를 선택할지 결정하므로 RS로 판정했다.",
    (1013, 2): "A1-Ring과 현행 Red Bull Ring은 단순한 날짜 범위가 아니라 서로 다른 circuit layout 대상이므로 RG로 판정했다.",
    (1054, 3): "FIFA/EA의 game attribute와 실제 경기 통계의 대체 metric 중 어느 자료와 속성을 의미하는지 결정하므로 RG로 판정했다.",
    (1132, 3): "전체·이름·성 중 어느 name field를 알파벳 정렬 기준으로 삼을지 결정하므로 RS로 판정했다.",
    (1200, 2): "ITP proxy와 명시적 thrombocytopenia 지표 중 어느 것을 세고, 분모를 thrombocytopenia 또는 SLE 환자로 둘지가 바뀌어 계산식이 달라지므로 MC로 판정했다.",
    (1450, 2): "'more than forty incomes'가 income record의 개수를 뜻하는지, 개별 income의 금액을 뜻하는지를 결정하는 질문이므로 RG로 판정했다. COUNT > 40과 amount > 40은 같은 metric의 대안 공식이 아니라 서로 다른 대상에 임계치를 적용한다.",
    (1508, 2): "특정 주유소 하나와 체코 내 모든 주유소 중 계산에 포함할 대상 집합을 결정하므로 SC로 판정했다.",
    (1522, 2): "특정 거래의 통화·총액이 가장 큰 통화·계정 기본 통화 중 어느 기준으로 하나의 통화를 선택할지 결정하므로 RS로 판정했다.",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def compact(text: str, limit: int = 190) -> str:
    normalized = " ".join((text or "").split()).replace('"', "'")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def rationale(row: dict[str, str], label: str) -> str:
    key = (int(row["question_id"]), int(row["turn"]))
    if key in RATIONALE_OVERRIDE:
        return RATIONALE_OVERRIDE[key]
    question = compact(row.get("clarification_question") or row["decision_context"])
    return (
        f"‘{question}’에서 선택지로 결정하려는 내용은 "
        f"{KOREAN_BOUNDARY[label]}에 해당하므로 {label}로 판정했다."
    )


def main() -> None:
    source_rows = [
        row
        for row in read_csv(INPUT)
        if row["architecture"] == "bif_only" and row["rq1_included"] == "1"
    ]
    source_rows.sort(key=lambda row: (int(row["question_id"]), int(row["turn"])))
    if len(source_rows) != 432:
        raise RuntimeError(f"Expected 432 included BIF turns, found {len(source_rows)}")

    actual_by_qid: dict[int, list[int]] = defaultdict(list)
    for row in source_rows:
        actual_by_qid[int(row["question_id"])].append(int(row["turn"]))
    if set(actual_by_qid) != set(REVIEWED_BY_QID):
        missing = sorted(set(actual_by_qid) - set(REVIEWED_BY_QID))
        extra = sorted(set(REVIEWED_BY_QID) - set(actual_by_qid))
        raise RuntimeError(f"Review qid mismatch: missing={missing}; extra={extra}")
    for qid, labels in REVIEWED_BY_QID.items():
        expected_turns = list(range(1, len(labels) + 1))
        if actual_by_qid[qid] != expected_turns:
            raise RuntimeError(
                f"Turn mismatch for qid {qid}: actual={actual_by_qid[qid]}, "
                f"review={expected_turns}"
            )

    output_rows: list[dict[str, Any]] = []
    for row in source_rows:
        qid = int(row["question_id"])
        turn = int(row["turn"])
        reviewed = REVIEWED_BY_QID[qid][turn - 1]
        initial = row["final_native_label"]
        output_rows.append(
            {
                "qid": qid,
                "turn_index": turn,
                "initial_label": initial,
                "reviewed_label": reviewed,
                "changed": int(initial != reviewed),
                "review_status": "질문·전체 선택지·turn context 전수 내용 재검토",
                "rationale": rationale(row, reviewed),
                "decision_context": row["decision_context"],
                "clarification_question": row["clarification_question"],
                "options_text": row["options_text"],
                "source_path": row["source_path"],
                "source_sha256": row["source_sha256"],
            }
        )

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = list(output_rows[0])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    changed = [row for row in output_rows if row["changed"]]
    print(f"rows={len(output_rows)} changed={len(changed)}")
    print("reviewed_distribution=" + str(dict(sorted(Counter(
        row["reviewed_label"] for row in output_rows
    ).items()))))
    print("changes=")
    for row in changed:
        print(
            f"  {row['qid']}:{row['turn_index']} "
            f"{row['initial_label']}->{row['reviewed_label']}"
        )
    print(f"output={OUTPUT}")


if __name__ == "__main__":
    main()
