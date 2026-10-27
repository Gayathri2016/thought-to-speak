"""Practice modes, their rubrics, and a starter prompt bank."""
import random

MODES = {
    "Everyday Conversation": {
        "group": "Everyday life",
        "goal": "Feel more comfortable talking with people in everyday situations.",
        "rubric": "A friendly opening; one relevant personal detail; an easy question that invites the other person "
                  "to respond. Natural conversation matters more than a polished speech or numbers.",
        "framework": ["Opening", "Detail", "Question"],
        "target_seconds": (30, 60),
        "prompts": [
            "Introduce yourself to someone you have just met at a gathering. Share an interest and ask about theirs.",
            "A neighbor asks how your weekend was. Share one highlight and ask about their weekend.",
            "Tell a friend about a hobby you enjoy and ask what they like doing in their free time.",
            "Recommend a meal, book, film, or place you enjoyed. Explain why and ask what they recommend.",
            "You are meeting someone new in a class or community group. Start a friendly conversation.",
            "A friend asks how your day went. Share something that happened and invite them to share too.",
        ],
    },
    "Tell a Story": {
        "group": "Everyday life",
        "goal": "Tell a personal story that is easy to follow.",
        "rubric": "Briefly set the scene; say what you did; explain how it turned out; close with what it meant "
                  "to you. Everyday experiences count; no professional achievement or numerical result is required.",
        "framework": ["Situation", "Action", "Result", "Lesson"],
        "target_seconds": (45, 90),
        "prompts": [
            "Tell a friend about a small mistake that taught you something.",
            "Describe a time someone helped you when you needed it.",
            "Tell the story of a day that did not go as planned.",
            "Share a memory of learning something for the first time.",
            "Describe a small challenge you overcame in everyday life.",
            "Tell someone about a moment that made you smile.",
        ],
    },
    "Share an Opinion": {
        "group": "Everyday life",
        "goal": "Express an opinion clearly and respectfully.",
        "rubric": "State your view; give a reason; support it with a familiar example; finish with your main "
                  "point. Respect other preferences; specialist knowledge and statistics are not required.",
        "framework": ["Point", "Reason", "Example", "Point again"],
        "target_seconds": (30, 75),
        "prompts": [
            "Would you rather spend a free day at home or outdoors? Explain your choice.",
            "Is it better to plan a trip carefully or leave room for surprises? Share your view.",
            "What is one small habit that makes everyday life better? Explain why.",
            "Would you rather learn from a book, a video, or by trying something yourself? Explain.",
            "Do you prefer cooking at home or eating out? Give a reason and an example.",
            "What makes someone a good friend? Choose one quality and explain why it matters.",
        ],
    },
    "Difficult Conversations": {
        "group": "Everyday life",
        "goal": "Make a request, set a boundary, or disagree without losing warmth.",
        "rubric": "Acknowledge the other person's perspective; state your need respectfully; explain why; "
                  "suggest a practical next step. Be clear without blaming or inventing the other person's motives.",
        "framework": ["Acknowledge", "Point", "Reason", "Next step"],
        "target_seconds": (30, 75),
        "prompts": [
            "A friend keeps arriving late. Explain how it affects you and agree on a plan for next time.",
            "You cannot attend a family event. Decline kindly without over-explaining.",
            "Ask a housemate to share a household chore more fairly.",
            "Your order at a cafe is wrong. Ask politely for it to be corrected.",
            "You and a friend want different weekend plans. Suggest a compromise.",
            "Someone keeps interrupting you. Ask for a chance to finish your thought respectfully.",
        ],
    },
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
        "selectable": False,  # Kept only for existing lessons and saved takes.
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

DEFAULT_MODE = "Everyday Conversation"


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
        {"name": k, "group": v.get("group", "Work & presentations"), "goal": v["goal"], "framework": v["framework"],
         "target_seconds": list(v["target_seconds"]), "prompts": v["prompts"],
         "selectable": v.get("selectable", True)}
        for k, v in MODES.items()
    ]
