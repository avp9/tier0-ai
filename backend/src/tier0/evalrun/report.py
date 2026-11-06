from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tier0.evalrun.runner import (
    CaseOutcome,
    GapOutcome,
    KnowledgeOutcome,
    percentile,
)
from tier0.evalrun.usage import Prices, Usage
from tier0.rag.evaluation import RetrievalReport, SeparationReport


@dataclass
class Results:
    retrieval: RetrievalReport
    separation: SeparationReport
    knowledge: list[KnowledgeOutcome]
    gaps: list[GapOutcome]
    cases: list[CaseOutcome]
    usage: Usage
    prices: Prices
    providers: dict[str, str] = field(default_factory=dict)


def _rate(passed: int, total: int) -> float | None:
    return passed / total if total else None


def summarize(results: Results) -> dict[str, Any]:
    """All numbers for the report. ``None`` means "nothing was measured", never zero."""
    kn, gaps, cases = results.knowledge, results.gaps, results.cases
    routing = [ok for c in cases for ok in c.routing_checks]
    routing += [k.routed_correctly for k in kn] + [g.routed_correctly for g in gaps]
    latencies = (
        [k.seconds for k in kn]
        + [g.seconds for g in gaps]
        + [s for c in cases for s in c.latencies]
    )

    def by_kind(kind: str) -> dict[str, Any]:
        mine = [c for c in cases if c.kind == kind]
        return {"cases": len(mine), "pass_rate": _rate(sum(c.passed for c in mine), len(mine))}

    retrieval = results.retrieval
    return {
        "providers": results.providers,
        "retrieval": {
            "cases": len(retrieval.results),
            "recall": {str(k): v for k, v in retrieval.mean_recall.items()},
            "hit": {str(k): v for k, v in retrieval.hit_rate.items()},
            "mrr": retrieval.mrr,
            "gap_score_max": results.separation.gap_max,
            "answerable_score_min": results.separation.answerable_min,
            "separable": results.separation.separable,
        },
        "knowledge": {
            "cases": len(kn),
            "answer_rate": _rate(sum(k.answered for k in kn), len(kn)),
            "cited_expected_source_rate": _rate(sum(k.cited_expected for k in kn), len(kn)),
            "false_escalation_rate": _rate(sum(not k.answered for k in kn), len(kn)),
        },
        "gaps": {
            "cases": len(gaps),
            "escalation_rate": _rate(sum(g.escalated for g in gaps), len(gaps)),
            "answered_wrongly_rate": _rate(sum(g.answered for g in gaps), len(gaps)),
        },
        "routing": {"checks": len(routing), "accuracy": _rate(sum(routing), len(routing))},
        "ticket_flows": by_kind("ticket_flow"),
        "status": by_kind("status"),
        "safety": by_kind("safety"),
        "routing_cases": by_kind("routing"),
        "latency_seconds": {
            "turns": len(latencies),
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "usage": {
            "chat_calls": results.usage.chat_calls,
            "embedding_calls": results.usage.embed_calls,
            "estimated_dollars": None
            if results.providers.get("llm") == "fake"
            else results.usage.estimate_dollars(results.prices),
            "note": "Estimate: characters/4 as tokens, placeholder prices; intent calls to "
            "JEV are not counted. No cost is shown for fake providers.",
        },
        "failures": [{"case": c.case_id, "problems": c.failures} for c in cases if not c.passed],
    }


def gate_failures(summary: dict[str, Any]) -> list[str]:
    """Hard rules that must hold on every run, whatever the model's quality."""
    problems = []
    safety = summary["safety"]["pass_rate"]
    if safety is not None and safety < 1.0:
        problems.append("a safety case failed (a ticket may exist without confirmation)")
    return problems


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def to_markdown(summary: dict[str, Any]) -> str:
    r, k, g = summary["retrieval"], summary["knowledge"], summary["gaps"]
    lat, use = summary["latency_seconds"], summary["usage"]
    dollars = use["estimated_dollars"]
    cost = "n/a" if dollars is None else f"${dollars:.3f}"
    lines = [
        "| Measure | Result |",
        "|---|---|",
        f"| Retrieval Recall@5 / Hit@5 / MRR ({r['cases']} cases) | "
        f"{r['recall'].get('5', 0):.3f} / {r['hit'].get('5', 0):.3f} / {r['mrr']:.3f} |",
        f"| Top score: lowest answerable vs highest gap question | "
        f"{r['answerable_score_min']:.3f} vs {r['gap_score_max']:.3f} "
        f"({'separable' if r['separable'] else 'overlap'}) |",
        f"| Answerable questions answered ({k['cases']}) | {_pct(k['answer_rate'])} |",
        f"| Answered and cited an expected source (groundedness proxy) | "
        f"{_pct(k['cited_expected_source_rate'])} |",
        f"| Answerable questions wrongly escalated | {_pct(k['false_escalation_rate'])} |",
        f"| Knowledge-gap questions escalated ({g['cases']}) | {_pct(g['escalation_rate'])} |",
        f"| Knowledge-gap questions answered anyway | {_pct(g['answered_wrongly_rate'])} |",
        f"| Routing accuracy ({summary['routing']['checks']} checks) | "
        f"{_pct(summary['routing']['accuracy'])} |",
        f"| Ticket flows passed ({summary['ticket_flows']['cases']}) | "
        f"{_pct(summary['ticket_flows']['pass_rate'])} |",
        f"| Status lookups passed ({summary['status']['cases']}) | "
        f"{_pct(summary['status']['pass_rate'])} |",
        f"| Safety cases passed ({summary['safety']['cases']}) | "
        f"{_pct(summary['safety']['pass_rate'])} |",
        f"| Latency per turn, p50 / p95 | {lat['p50']:.2f} s / {lat['p95']:.2f} s |",
        f"| Model calls / estimated cost | {use['chat_calls']} chat, {use['embedding_calls']} "
        f"embedding / {cost} |",
    ]
    out = "\n".join(lines)
    if summary["failures"]:
        out += "\n\n**Scripted cases that failed**\n\n" + "\n".join(
            f"- `{f['case']}`: " + "; ".join(f["problems"]) for f in summary["failures"]
        )
    return out + f"\n\n_{use['note']}_\n"
