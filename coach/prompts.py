"""Practice modes, their rubrics, and a starter prompt bank."""
import random

MODES = {
    "Interview": {
        "goal": "Answer a behavioral or situational interview question.",
        "rubric": "STAR shape (Situation, Task, Action, Result); personal ownership ('I' not only 'we'); "
                  "a concrete, preferably measurable result; a closing lesson or relevance to the role.",
        "framework": ["Situation", "Task", "Action", "Result", "Lesson"],
        "target_seconds": (60, 120),
        "prompts": [
            "Tell me about a time you disagreed with a senior stakeholder. What did you do?",
            "Describe a project that failed or slipped. What was your part in it?",
            "Tell me about the hardest technical problem you solved in the last year.",
            "Walk me through a time you had to deliver with incomplete information.",
            "Tell me about a time you raised the bar for your team.",
            "Why are you interested in this role, and why now?",
        ],
    },
    "Leadership": {
        "goal": "Communicate a decision, direction, or difficult message to a team or leaders.",
        "rubric": "Lead with the decision or ask; the 'why' tied to goals; trade-offs acknowledged; "
                  "clear owners and next steps; calm, confident language without hedging.",
        "framework": ["Decision / Ask", "Why", "Trade-offs", "Next steps"],
        "target_seconds": (45, 90),
        "prompts": [
            "Announce to your team that a priority project is being deprioritized.",
            "Pitch your director on headcount for a new initiative in 60 seconds.",
            "Give an update to leadership on a project that is two weeks behind.",
            "Explain to a peer team why you are saying no to their request this quarter.",
            "Kick off a new quarter: what matters most and why?",
            "Give constructive feedback to a strong engineer who keeps missing reviews.",
        ],
    },
    "Technical Explanation": {
        "goal": "Explain a technical concept or design clearly to the stated audience.",
        "rubric": "One-sentence definition first; a concrete example or analogy; how it works in 2-3 steps; "
                  "trade-offs or when not to use it; audience-appropriate vocabulary.",
        "framework": ["Definition", "Example / analogy", "How it works", "Trade-offs"],
        "target_seconds": (60, 120),
        "prompts": [
            "Explain exactly-once processing in stream processing to a product manager.",
            "Explain how a vector database powers retrieval-augmented generation.",
            "Explain the difference between Kafka and Kinesis to a new engineer.",
            "Explain backpressure and why it matters in a streaming pipeline.",
            "Explain what an AI agent harness is to a non-technical executive.",
            "Explain how you would design a rate limiter for a public API.",
        ],
    },
    "Toastmasters": {
        "goal": "Deliver an impromptu Table Topics style speech.",
        "rubric": "A hook in the first sentence; one clear message; a short story or example; "
                  "signposted body (2-3 points); a memorable close that calls back to the opening.",
        "framework": ["Hook", "Message", "Story / points", "Call-back close"],
        "target_seconds": (60, 120),
        "prompts": [
            "What is one habit that changed your life?",
            "If you could master any skill instantly, what would it be?",
            "Describe a moment when a stranger changed your perspective.",
            "Is it better to be a specialist or a generalist?",
            "What does success look like to you ten years from now?",
            "Tell us about a risk you are glad you took.",
        ],
    },
    "FDE / Customer": {
        "goal": "Handle a customer-facing conversation as a forward-deployed engineer or solutions architect.",
        "rubric": "Acknowledge the customer's goal or concern; restate the problem in their terms; "
                  "a concrete recommendation with rationale; risks and mitigations; a clear next step and owner.",
        "framework": ["Acknowledge", "Restate problem", "Recommendation", "Risks", "Next step"],
        "target_seconds": (45, 120),
        "prompts": [
            "A customer says your platform is too expensive compared to building in-house. Respond.",
            "The customer's CTO asks why the pilot missed its latency target. Explain and propose a path.",
            "Explain to a customer why their GenAI prototype should not go to production yet.",
            "A customer wants a feature on your roadmap next month. It is not planned. Respond.",
            "Open a discovery call: learn about the customer's data platform pain points.",
            "Summarize a successful pilot to an executive sponsor and ask for expansion.",
        ],
    },
}

DEFAULT_MODE = "Interview"


def normalize_mode(mode: str | None) -> str:
    if not mode:
        return DEFAULT_MODE
    for m in MODES:
        if m.lower() == mode.strip().lower():
            return m
    # tolerate legacy/short names
    aliases = {"technical": "Technical Explanation", "fde": "FDE / Customer", "customer": "FDE / Customer"}
    return aliases.get(mode.strip().lower(), DEFAULT_MODE)


def random_prompt(mode: str, exclude: str | None = None) -> str:
    bank = [p for p in MODES[normalize_mode(mode)]["prompts"] if p != exclude]
    return random.choice(bank)


def public_modes():
    return [
        {"name": k, "goal": v["goal"], "framework": v["framework"],
         "target_seconds": list(v["target_seconds"]), "prompts": v["prompts"]}
        for k, v in MODES.items()
    ]
