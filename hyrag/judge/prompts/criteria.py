"""judge/prompts/criteria.py — the rubric, versioned in git and copied beside the table.

**The benchmark's rubric is the papers' three criteria** — **Comprehensiveness**,
**Diversity**, **Empowerment** (Edge et al.'s LLM-judged dimensions for long-form retrieval
answers; Guo et al. judge the same three). Decided 2026-10-16, superseding the earlier
"three plus a Directness control" design: the control dominated the ranking (the identical
comparisons flip from the baseline winning 82/76/50% to kg-graph winning 78/66/100% when it
is removed), so a table carrying it was a table about padding, not about the augmentations.
**The control run is kept on disk as a sensitivity variant**
(`results/judge_pairwise.jsonl`), not deleted — it is what tells a reader how much of the
ranking a padding penalty carries.

One criterion is ours and is **not** part of the rubric: **Correctness**, gold-anchored and
scored for factoid and multi-hop only, reported as an accuracy column beside the rubric. The
papers do not judge correctness; a benchmark that claims to measure answer quality should
show it, and keeping it out of the composite is what lets both be true.

Two deliberate omissions, both of them decisions rather than oversights:

* **No ground-truth similarity for the thematic class.** Its "gold answer" is a checklist of
  themes *we* wrote from the same corpus; scoring similarity to it measures agreement with
  us. The thematic questions are judged on the rubric alone.
* **No retrieval context in the pointwise prompt.** Rows retrieve 1 to 196 chunks; a judge
  that sees one row's 196 chunks and another's 3 is measuring context volume, not answer
  quality (§7.2 trap ii). Retrieval is measured deterministically instead — evidence-target
  hit rate and citation resolution (`citations.py`), both of which are facts, not opinions.

`RUBRIC_VERSION` is what the pins record and what the report cites; change the text and the
version must change, or a table produced under an older rubric will read as if it were this
one. The 0–10 anchors are explicit because DeepEval's `GEval` parses an integer in `[0, 10]`
and normalises it to `[0, 1]`; an unanchored scale drifts between calls.
"""

from __future__ import annotations

RUBRIC_VERSION = "judge-rubric-1"

CRITERIA = (
    {
        "key": "correctness",
        "name": "Correctness",
        "role": "accuracy",  # reported beside the rubric, never inside it
        # The only criterion that reads the gold answer, and only where a gold answer is a
        # string: factoid and multi-hop. The thematic gold is a checklist we wrote from the
        # same corpus, so grading a thematic answer against it would measure agreement with us.
        "classes": ("factoid", "multi-hop"),
        "criteria": (
            "The response states the fact or the explanation the expected answer requires, "
            "without contradicting it or substituting a different fact."
        ),
        "evaluation_steps": [
            "Read the expected output and identify the facts it requires: a value, a cause, a "
            "distinction between two things, or a set of reasons.",
            "Check the response against each required fact: present and consistent, absent, "
            "or contradicted.",
            "Treat a response that states a different value, a different cause, or the wrong "
            "entity as incorrect even when it is fluent and well cited.",
            "Do not reward extra correct material: this criterion is about the required facts, "
            "not about volume.",
            "Award 0 when the required fact is missing or contradicted, 5 when part of a "
            "multi-part answer is right, 10 when every required fact is present and consistent.",
        ],
    },
    {
        "key": "comprehensiveness",
        "name": "Comprehensiveness",
        "criteria": (
            "The response covers the substance of the question: it addresses every distinct "
            "aspect that was asked about, with the specificity the subject allows, rather "
            "than developing one aspect and leaving the others untouched."
        ),
        "evaluation_steps": [
            "Identify the distinct aspects of the input: the entities named, what is being "
            "asked (a value, a cause, a comparison, a set of themes), and any sub-questions.",
            "For each aspect, note what the response provides — nothing, a bare mention, or "
            "an explained answer.",
            "If an expected output is provided, treat the aspects it covers as the minimum "
            "set the response should address; covering more is in its favour.",
            "Coverage outweighs length: a long response that exhausts one aspect while "
            "ignoring the rest scores below a shorter one that covers them all.",
            "Award 0 when no aspect is addressed, 5 when about half the asked-for substance "
            "is present, 10 when every aspect is addressed with specific, non-redundant detail.",
        ],
    },
    {
        "key": "diversity",
        "name": "Diversity",
        "criteria": (
            "The response offers more than one perspective on the question: distinct "
            "insights, mechanisms, causes or contexts, instead of restating a single point "
            "in different words."
        ),
        "evaluation_steps": [
            "List the distinct points the response makes, treating a restatement as the same "
            "point rather than a new one.",
            "For each point, ask whether it adds a different mechanism, cause, consequence, "
            "or viewpoint — or only more words about an existing one.",
            "Specific material from different documents, systems, operators or failure modes "
            "counts strongly; generic safety language and disclaimers count for nothing.",
            "Length is not diversity: padding, repetition and enumeration of near-identical "
            "examples do not raise the score.",
            "Award 0 for a single point restated, 5 for two or three genuinely distinct "
            "points, 10 for several distinct and relevant insights on the question.",
        ],
    },
    {
        "key": "empowerment",
        "name": "Empowerment",
        "criteria": (
            "The response leaves the reader able to use it: it explains mechanisms and "
            "consequences well enough to act on the answer, judge a situation, or know what "
            "to check next."
        ),
        "evaluation_steps": [
            "Assume the reader must act on the response without reading the source documents.",
            "Reward explanations that connect cause to effect and state why the detail "
            "matters; penalise assertions with no rationale and unexplained jargon.",
            "Reward the response that names the conditions, limits or exceptions a reader "
            "would need to apply the answer correctly.",
            "Penalise material that is technically present but unusable — an unqualified "
            "number, an unnamed source, a recommendation without a reason.",
            "Award 0 when the reader learns nothing usable, 5 when the response is usable "
            "with effort, 10 when a reader could act or judge confidently on it.",
        ],
    },
    {
        "key": "directness",
        "name": "Directness",
        "role": "sensitivity",  # not in the rubric (2026-10-16); kept for the control run
        "criteria": (
            "The response addresses the question that was asked, in the question's own "
            "terms, without padding it with material the question did not invite."
        ),
        "evaluation_steps": [
            "State in one sentence what the input asks for, then check whether the response "
            "answers that thing.",
            "Penalise padding: restatements of the question, background the question did not "
            "ask for, generic introductions or conclusions, hedging, and meta-commentary "
            "about the response itself or the documents it used.",
            "Judge removal, not length: a long response whose every paragraph does work for "
            "the question is direct; a short one that dodges the question is not.",
            "Do not reward extra material merely because it is true or interesting — only "
            "material the question asked for counts.",
            "Award 0 when the response evades the question or buries it in unrequested "
            "material, 5 when the answer is present but surrounded by padding, 10 when it "
            "answers exactly and without waste.",
        ],
    },
)

RUBRIC = tuple(item["key"] for item in CRITERIA if item.get("role") is None)   # C · D · E
ACCURACY = tuple(item["key"] for item in CRITERIA if item.get("role") == "accuracy")
SENSITIVITY = tuple(item["key"] for item in CRITERIA if item.get("role") == "sensitivity")


def for_class(question_class: str) -> tuple[dict, ...]:
    """The criteria that apply to one question class — gold-anchored only where a gold exists.

    Excludes the sensitivity criterion: a scored answer is scored on the rubric plus the
    accuracy check, and the control lives in its own run (`runner.run_pairwise("control")`).
    """
    classes = ("factoid", "multi-hop", "thematic")
    return tuple(item for item in CRITERIA
                 if item.get("role") != "sensitivity"
                 and question_class in item.get("classes", classes))

PAIRWISE = {
    "name": "Overall quality",
    "criteria": (
        "Choose the response that better serves someone who needs the question answered. "
        "Weigh the four dimensions equally: comprehensiveness (does it cover what was "
        "asked), diversity (does it offer distinct insights rather than one restated), "
        "empowerment (could a reader act on it), and directness (does it answer the question "
        "rather than pad it). A longer response is not a better one."
    ),
    "evaluation_steps": [
        "Read the question and both candidate responses.",
        "Compare the candidates on comprehensiveness, diversity, empowerment and directness.",
        "Prefer the candidate that answers the question; treat padding, repetition and "
        "unrequested background as faults, not as thoroughness.",
        "If one candidate is clearly better overall, name it. If they are genuinely "
        "equivalent, name the one with fewer faults.",
    ],
}

# The same comparison with **the papers' three criteria only** — no control. Edge et al. and
# Guo et al. both judge comprehensiveness, diversity and empowerment, all three of which
# reward more material. Running both variants over the same answers isolates what the control
# does to the ranking; it is a protocol-sensitivity check, not the table's headline (the
# table uses `PAIRWISE`).
PAIRWISE_PAPER3 = {
    "name": "Paper criteria",
    "criteria": (
        "Choose the response that better serves someone who needs the question answered, on "
        "the three dimensions the augmentation papers judge: comprehensiveness (does it cover "
        "what was asked), diversity (does it offer distinct insights rather than one "
        "restated), and empowerment (could a reader act on it)."
    ),
    "evaluation_steps": [
        "Read the question and both candidate responses.",
        "Compare the candidates on comprehensiveness, diversity and empowerment.",
        "Prefer the candidate that covers more, more variously, and more usefully.",
        "If one candidate is clearly better, name it. If they are genuinely equivalent, name "
        "the one with fewer faults.",
    ],
}

# The blinding control (trap i): blinding is structural, but a 26,000-character report is not
# a paragraph, and style leaks identity. This asks the judge to do what a reader would do —
# guess the system from the answer — and the score is how often it can.
BLINDING_SYSTEMS = {
    "A": "a naive retrieval baseline: dense embeddings over the same document chunks, top "
         "passages retrieved, one generation call to write the answer",
    "B": "LightRAG: a graph of entities and relations extracted from the chunks, plus "
         "keywords extracted at query time, answering from the graph and the chunks",
    "C": "GraphRAG: community summaries built over the entity graph, synthesised into a "
         "report answering the question at the level of themes",
}

BLINDING_PROMPT = """Three systems answered the same question. Each system has a distinct \
method, which is how a careful reader might tell them apart:

- **System A** is {A}
- **System B** is {B}
- **System C** is {C}

Below are the three answers, unlabelled and in random order. For each candidate, say which \
system produced it. Use the method each system uses as your evidence: what it retrieves, \
what it reasons over, and the shape of what it writes. Say `"unknown"` when the answer \
carries no signal about its origin — a guess is worse than an admission here, and saying \
`"unknown"` is not penalised.

Question:
{question}

{candidates}

Return JSON only:
{{"assignments": [{{"candidate": 1, "system": "A"}}, {{"candidate": 2, "system": "B"}}, \
{{"candidate": 3, "system": "C"}}], "note": "one sentence on what gave each away, or what \
did not"}}
"""


def as_dict() -> dict:
    """The instantiation copied beside the table (`results/judge_prompts.json`)."""
    return {
        "rubric_version": RUBRIC_VERSION,
        "criteria": {item["key"]: {"name": item["name"], "criteria": item["criteria"],
                                   "classes": list(item.get("classes", ("factoid", "multi-hop", "thematic"))),
                                   "evaluation_steps": item["evaluation_steps"]}
                     for item in CRITERIA},
        "rubric": list(RUBRIC),
        "accuracy": list(ACCURACY),
        "sensitivity": list(SENSITIVITY),
        "pairwise_paper_rubric": PAIRWISE_PAPER3,   # the benchmark's headline
        "pairwise_with_control": PAIRWISE,          # sensitivity: adds a padding penalty
        "blinding_systems": BLINDING_SYSTEMS,
        "blinding_prompt": BLINDING_PROMPT,
    }
