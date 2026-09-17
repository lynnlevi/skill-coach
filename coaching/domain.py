from copy import deepcopy
import math

from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "coaching-v1"
MAX_ANSWER_CHARS = 12000
MAX_AUDIO_BYTES = 24_000_000

DEFAULT_CONFIG = {
    "training_goal": "Communicate clearly and confidently in professional conversations.",
    "context": "",
    "rubric": [
        {"name": "Structure", "weight": 30, "description": "Clear opening, logical reasoning, and conclusion."},
        {"name": "Clarity", "weight": 20, "description": "Concrete language that is easy to follow."},
        {"name": "Relevance", "weight": 20, "description": "Directly answers the question."},
        {"name": "Concision", "weight": 20, "description": "Focused response without unnecessary context."},
        {"name": "Evidence", "weight": 10, "description": "Specific examples and outcomes support the claims."},
    ],
    "coaching_instructions": "Be supportive and specific. Prefer a direct answer, reasoning, evidence, then outcome.",
}

QUESTION_BANK = [
    ("Explain a difficult decision you made. What did you choose, why, and what happened?", "Decision making"),
    ("Tell me about a time you disagreed with a colleague. How did you move forward?", "Collaboration"),
    ("Describe a project that did not go to plan. What did you learn and change?", "Reflection"),
    ("How would you explain a complex idea from your work to someone unfamiliar with it?", "Communication"),
    ("Tell me about an achievement you are proud of. What was your specific contribution?", "Evidence"),
    ("When several priorities compete for your time, how do you decide what to do first?", "Prioritization"),
    ("Describe a time you received difficult feedback. What did you do with it?", "Growth"),
    ("How would you persuade a stakeholder who disagrees with your recommendation?", "Influence"),
    ("How would you prioritize a product roadmap when stakeholders disagree?", "Product management"),
    ("Tell me about a product you launched. How did you measure its success?", "Product management"),
]


def validate_config(config: dict) -> dict:
    clean = {}
    for name, limit in (("training_goal", 2000), ("context", 8000), ("coaching_instructions", 4000)):
        clean[name] = str(config.get(name, "")).strip()
        if len(clean[name]) > limit:
            raise ValueError(f"{name.replace('_', ' ').title()} must be at most {limit} characters.")
    if not clean["training_goal"]:
        raise ValueError("Please enter a training goal.")
    rubric = config.get("rubric")
    if not isinstance(rubric, list) or not 1 <= len(rubric) <= 12:
        raise ValueError("Use 1–12 rubric criteria.")
    rows, seen = [], set()
    for row in rubric:
        name = str(row.get("name") or "").strip()
        description = str(row.get("description") or "").strip()
        try:
            weight = float(row.get("weight", 0))
        except (TypeError, ValueError):
            raise ValueError("Each criterion needs a numeric weight.") from None
        if not name or len(name) > 80 or name.casefold() in seen:
            raise ValueError("Criterion names must be unique and 1–80 characters long.")
        if not math.isfinite(weight) or not 0 < weight <= 100:
            raise ValueError("Each weight must be greater than 0 and at most 100.")
        if len(description) > 1000:
            raise ValueError("Criterion descriptions must be at most 1,000 characters.")
        seen.add(name.casefold())
        rows.append({"name": name, "weight": weight, "description": description})
    if not math.isclose(sum(r["weight"] for r in rows), 100, abs_tol=0.01):
        raise ValueError("Rubric weights must add up to 100%.")
    clean["rubric"] = rows
    return deepcopy(clean)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DimensionScore(StrictModel):
    name: str
    score: float = Field(ge=0, le=10)
    feedback: str


class Evaluation(StrictModel):
    summary: str
    dimension_scores: list[DimensionScore]
    strengths: list[str]
    improvements: list[str]
    suggested_approach: str
    tags: list[str]


class QuestionChoice(StrictModel):
    question_id: int
    reason: str


class TrendAnalysis(StrictModel):
    summary: str
    strengths: list[str]
    recurring_weaknesses: list[str]
    trends: list[str]
    recommendations: list[str]
    next_training_focus: list[str]
    limitations: str


def score_evaluation(evaluation: Evaluation, config: dict) -> dict:
    names = [row.name for row in evaluation.dimension_scores]
    expected = [row["name"] for row in config["rubric"]]
    if len(names) != len(expected) or set(names) != set(expected):
        raise ValueError("The evaluation did not cover every rubric criterion exactly once. Please retry.")
    scores = {row.name: row.score for row in evaluation.dimension_scores}
    if any(not math.isfinite(score) for score in scores.values()):
        raise ValueError("The evaluation contained an invalid score. Please retry.")
    result = evaluation.model_dump()
    result["overall_score"] = round(sum(scores[r["name"]] * r["weight"] for r in config["rubric"]) / 100, 2)
    return result
