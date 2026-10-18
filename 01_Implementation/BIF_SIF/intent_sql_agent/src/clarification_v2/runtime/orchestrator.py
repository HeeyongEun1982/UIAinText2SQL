from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from clarification_v2.models.decision import RunResult
from clarification_v2.runtime.decision_discovery.contract_coverage import semantic_key
from clarification_v2.runtime.decision.typed_effects import apply_typed_effect


class ClarificationOrchestrator:
    """Agent 1 / authority boundary.

    Only this class owns queue mutation, Decision registry, CQD integration,
    dependency reconciliation, rediscovery, discovery audit, and termination.
    No Gold/SQL/schema input exists in this API.
    """

    def __init__(self, *, source_analyzer, initial_builder, contract_coverage, schema_linker,
                 physical_grounder, technical_clarifier,
                 discovery, discovery_auditor,
                 queue, clarification_agent, reducer, reconciler, ledger, validator,
                 renderer, freezer, logger, max_rounds: int | None = None,
                 semantic_only: bool = False) -> None:
        self.source_analyzer = source_analyzer
        self.initial_builder = initial_builder
        self.contract_coverage = contract_coverage
        self.schema_linker = schema_linker
        self.physical_grounder = physical_grounder
        self.technical_clarifier = technical_clarifier
        self.discovery = discovery
        self.discovery_auditor = discovery_auditor
        self.queue = queue
        self.clarification_agent = clarification_agent
        self.reducer = reducer
        self.reconciler = reconciler
        self.ledger = ledger
        self.validator = validator
        self.renderer = renderer
        self.freezer = freezer
        self.logger = logger
        self.max_rounds = max_rounds
        self.semantic_only = semantic_only

    def run(self, *, question_id: str, original_question: str) -> RunResult:
        self.ledger._events = []
        self.logger.write(
            "00_input/original_question.json",
            {"question_id": question_id, "original_question": original_question},
        )

        analysis = self.source_analyzer.analyze(
            question_id=question_id,
            original_question=original_question,
        )
        self.logger.write("01_source_analysis/source_analysis.json", analysis)

        cqd = self.initial_builder.build(analysis)
        self.validator.validate_initial_semantics(cqd)
        initial_cqd = deepcopy(cqd)
        initial_text = self.renderer.render(initial_cqd)
        self.logger.write("02_initial_cqd/initial_cqd.json", initial_cqd)
        self.logger.write("02_initial_cqd/initial_cqd.txt", initial_text)

        # Independent initial audit: catches material ambiguities the first source-analysis
        # pass may have missed. It still sees no Gold/SQL/schema information.
        initial_audit_hints = self.discovery_auditor.audit(
            analysis=analysis, cqd=cqd, decision_events=[]
        )
        existing_hint_ids = {h.hint_id for h in analysis.ambiguity_hints}
        for hint in initial_audit_hints:
            if hint.hint_id not in existing_hint_ids:
                analysis.ambiguity_hints.append(hint)
                existing_hint_ids.add(hint.hint_id)
        self.logger.write(
            "01_source_analysis/initial_discovery_audit.json", initial_audit_hints
        )
        self.logger.write(
            "01_source_analysis/source_analysis_after_audit.json", analysis
        )

        # Source/auditor hints are evidence, not discovery authority. Every registered
        # contract independently returns one explicit assessment before queue creation.
        contract_assessments = self.contract_coverage.inspect(analysis=analysis, cqd=cqd)
        self.logger.write(
            "03_decisions/clarification_contract_coverage.json", contract_assessments
        )

        link_assessments = self.schema_linker.inspect(
            contract_assessments=contract_assessments, cqd=cqd
        )
        self.logger.write("03_decisions/schema_link_assessments.json", link_assessments)
        grounding_events = []
        for index, link in enumerate(link_assessments, start=1):
            if link.status != "grounded" or not link.typed_effect:
                continue
            cqd_before_link = deepcopy(cqd)
            apply_typed_effect(cqd, link.typed_effect)
            event = {
                "grounding_id": f"grounding-{index}",
                "family": link.family,
                "source_span": link.source_span,
                "reason": link.reason,
                "typed_effect": link.typed_effect,
            }
            grounding_events.append(event)
            self.logger.write(f"03_decisions/grounding_{index:02d}_before.json", cqd_before_link)
            self.logger.write(f"03_decisions/grounding_{index:02d}_after.json", cqd)
        cqd.setdefault("traceability", {})["grounding_event_ids"] = [
            event["grounding_id"] for event in grounding_events
        ]
        self.logger.write("03_decisions/grounding_events.json", grounding_events)
        self.logger.write("03_decisions/cqd_after_schema_link.json", cqd)

        grounded_keys = {
            (item.family, item.source_span, item.target_path)
            for item in link_assessments if item.status == "grounded"
        }
        grounded_spans = [
            item.source_span for item in link_assessments if item.status == "grounded"
        ]

        def overlaps_grounded_span(source_span: str) -> bool:
            normalized = " ".join(source_span.casefold().split())
            return any(
                normalized in " ".join(span.casefold().split())
                or " ".join(span.casefold().split()) in normalized
                for span in grounded_spans
            )
        detected_hints = self.contract_coverage.detected_hints(contract_assessments)
        analysis.ambiguity_hints = [
            hint for hint in detected_hints
            if (hint.family, hint.source_span, hint.target_path) not in grounded_keys
        ]
        self.logger.write(
            "01_source_analysis/source_analysis_after_contracts.json", analysis
        )

        decision_registry = {}
        known_keys: set[tuple[str, str]] = set()

        def register_new_decisions() -> None:
            new = self.discovery.discover(
                analysis=analysis,
                cqd=cqd,
                known_keys=known_keys,
            )
            for d in new:
                key = semantic_key(d.family, d.source_span)
                known_keys.add(key)
                decision_registry[d.decision_id] = d

        register_new_decisions()
        initial_decisions = [d.to_dict() for d in decision_registry.values()]
        self.logger.write("03_decisions/initial_decisions.json", initial_decisions)
        rounds = 0
        turn_records: list[dict] = []

        def _finalize_unresolved_cqd_nodes(value) -> None:
            if isinstance(value, dict):
                if value.get("resolution_status") == "unresolved":
                    value["resolution_status"] = "confirmed"
                if "unresolved_ref" in value:
                    value.pop("unresolved_ref", None)
                for k, v in list(value.items()):
                    if isinstance(v, str) and v.strip().lower() in {"unresolved", "unknown_pending_clarification"}:
                        value[k] = value.get("source_text", "") or "confirmed_intent"
                    else:
                        _finalize_unresolved_cqd_nodes(v)
            elif isinstance(value, list):
                for item in value:
                    _finalize_unresolved_cqd_nodes(item)

        while True:
            if self.max_rounds is not None and rounds >= self.max_rounds:
                self.logger.write(
                    "04_queue/round_limit_reached.json",
                    {
                        "configured_max_rounds": self.max_rounds,
                        "round": rounds,
                        "action": "fast_path_exit_to_confirmed_cqd",
                    },
                )
                self.contract_coverage.validate_complete(contract_assessments)
                self.schema_linker.validate_complete(contract_assessments, link_assessments)
                break

            decisions = list(decision_registry.values())
            decisions = self.reconciler.reconcile(
                decisions=decisions,
                resolved_ids=self.ledger.resolved_ids(),
            )
            decisions = self.discovery.reassess_pending(cqd=cqd, decisions=decisions)
            self.queue.replace(decisions)
            self.logger.write(f"04_queue/round_{rounds:02d}.json", self.queue.snapshot())
            decision = self.queue.next()
            if decision is None:
                # An empty queue is a valid terminal condition only after all registered
                # contracts produced a well-formed assessment.
                self.contract_coverage.validate_complete(contract_assessments)
                self.schema_linker.validate_complete(contract_assessments, link_assessments)
                break

            rounds += 1
            cqd_before = deepcopy(cqd)
            turn_dir = f"05_clarification/turn_{rounds:02d}"
            self.logger.write(f"{turn_dir}/decision.json", decision)

            resolution = self.clarification_agent.resolve(decision)
            while resolution.mapping != "selected":
                resolution = self.clarification_agent.resolve(decision)
            self.logger.write(
                f"{turn_dir}/question.json",
                {"decision_id": decision.decision_id, "question_text": resolution.question_text},
            )
            self.logger.write(
                f"{turn_dir}/e2_answer.json",
                {"decision_id": decision.decision_id, "answer_text": resolution.answer_text},
            )
            self.logger.write(f"{turn_dir}/interpretation.json", resolution)

            event = self.reducer.reduce(cqd=cqd, decision=decision, resolution=resolution)
            self.ledger.append(event)
            self.logger.write(f"{turn_dir}/decision_event.json", event)
            self.logger.write(f"{turn_dir}/cqd_before.json", cqd_before)
            self.logger.write(f"{turn_dir}/cqd_after.json", cqd)

            turn_records.append({
                "round": rounds,
                "decision_id": decision.decision_id,
                "family": decision.family,
                "source_span": decision.source_span,
                "candidates": [
                    {"candidate_id": c.candidate_id, "user_summary": c.user_summary}
                    for c in decision.candidates
                ],
                "question_text": resolution.question_text,
                "answer_text": resolution.answer_text,
                "mapping": resolution.mapping,
                "selected_candidate_id": resolution.selected_candidate_id,
                "confidence": resolution.confidence,
                "cqd_after_text": self.renderer.render(cqd),
            })

            # Post-answer discovery audit can expose NEW decisions, but may not rewrite CQD.
            if self.max_rounds is None or rounds < self.max_rounds:
                new_hints = self.discovery_auditor.audit(
                    analysis=analysis,
                    cqd=cqd,
                    decision_events=self.ledger.events(),
                )
                existing_hint_ids = {h.hint_id for h in analysis.ambiguity_hints}
                existing_meanings = {
                    semantic_key(h.family, h.source_span) for h in analysis.ambiguity_hints
                }
                existing_targets = {h.target_path for h in analysis.ambiguity_hints}
                for hint in new_hints:
                    meaning = semantic_key(hint.family, hint.source_span)
                    if (
                        hint.hint_id not in existing_hint_ids
                        and meaning not in existing_meanings
                        and hint.target_path not in existing_targets
                        and not overlaps_grounded_span(hint.source_span)
                    ):
                        analysis.ambiguity_hints.append(hint)
                        existing_hint_ids.add(hint.hint_id)
                        existing_meanings.add(meaning)
                        existing_targets.add(hint.target_path)
                register_new_decisions()

        # Finalize and validate confirmed CQD
        _finalize_unresolved_cqd_nodes(cqd)
        self.schema_linker.validate_grounded_state(link_assessments, cqd)
        self.validator.validate_confirmable(cqd)
        rendered = self.renderer.render(cqd)
        if not rendered.strip():
            raise ValueError("Confirmed CQD rendered text is empty")
        confirmed = self.freezer.freeze(cqd, rendered)
        self.logger.write("06_final/confirmed_cqd.json", confirmed)
        self.logger.write("06_final/confirmed_cqd.txt", rendered)
        self.logger.write("07_ledger/decision_events.json", self.ledger.events())
        self.logger.write("07_ledger/clarification_turns.json", turn_records)

        if self.semantic_only:
            return RunResult(
                question_id=question_id,
                original_question=original_question,
                initial_cqd=initial_cqd,
                confirmed_cqd=confirmed,
                decision_events=self.ledger.events(),
                rounds=rounds,
                log_id=self.logger.log_id or "",
                source_analysis=asdict(analysis),
                contract_coverage=[asdict(item) for item in contract_assessments],
                link_assessments=[asdict(item) for item in link_assessments],
                grounding_events=grounding_events,
                initial_decisions=initial_decisions,
                clarification_turns=turn_records,
                initial_cqd_text=initial_text,
                confirmed_cqd_text=rendered,
            )

        physical_plan = self.physical_grounder.build(cqd=confirmed)
        self.logger.write("08_bif_compile/bif_leaves.json", physical_plan.get("bif_leaves", []))
        self.logger.write("08_bif_compile/logical_query_ir.json", physical_plan.get("logical_ir", {}))
        self.logger.write(
            "08_bif_compile/bif_coverage.json",
            physical_plan.get("logical_ir", {}).get("coverage", []),
        )
        self.logger.write("09_schema_candidates/candidates.json", physical_plan.get("schema_candidates", {}))
        self.logger.write("10_grounding/grounding.json", physical_plan.get("grounding", {}))
        self.logger.write("12_relational_plan/plan.json", physical_plan.get("relational_ast", {}))
        self.logger.write("12_relational_plan/post_bif_result.json", physical_plan)
        technical_turns = self.technical_clarifier.resolve_user_gaps(
            plan=physical_plan, grounding_service=self.physical_grounder, cqd=cqd
        )
        # Technical clarification may refine a business node when schema grounding
        # exposes a distinction that the earlier question did not settle precisely.
        self.validator.validate_confirmable(cqd)
        rendered = self.renderer.render(cqd)
        confirmed = self.freezer.freeze(cqd, rendered)
        self.logger.write("06_final/confirmed_cqd.json", confirmed)
        self.logger.write("06_final/confirmed_cqd.txt", rendered)
        self.logger.write("11_technical_clarification/turns.json", technical_turns)
        self.logger.write("12_relational_plan/post_bif_result_after_clarification.json", physical_plan)

        return RunResult(
            question_id=question_id,
            original_question=original_question,
            initial_cqd=initial_cqd,
            confirmed_cqd=confirmed,
            decision_events=self.ledger.events(),
            rounds=rounds,
            log_id=self.logger.log_id or "",
            source_analysis=asdict(analysis),
            contract_coverage=[asdict(item) for item in contract_assessments],
            link_assessments=[asdict(item) for item in link_assessments],
            grounding_events=grounding_events,
            physical_plan=physical_plan,
            technical_clarification_turns=technical_turns,
            initial_decisions=initial_decisions,
            clarification_turns=turn_records,
            initial_cqd_text=initial_text,
            confirmed_cqd_text=rendered,
        )
