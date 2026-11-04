"""Deterministic checks on the writer's draft, before anyone sees it.

1. Every placeholder points to a real result and field.
2. No raw numbers, except years, quarter names and ISO dates.
3. Direction words (rose, fell...) match the sign of the change they describe.
4. Every result used was produced in this request, for this role.
5. No hidden column names or hidden values (emails, customer names).
6. No causal claims: there is no evidence for a cause yet (driver analysis is phase 6).

Returns a list of problems; empty means the draft passes.
"""

import re

from analyst.facts import PLACEHOLDER, key_of

UP = {"rose", "rise", "rises", "risen", "rising", "increased", "increase", "increases", "grew",
      "grow", "grows", "growth", "climbed", "gained", "up", "higher", "jumped", "jump"}
DOWN = {"fell", "fall", "falls", "fallen", "falling", "dropped", "drop", "drops", "decreased",
        "decrease", "decreases", "declined", "decline", "declines", "shrank", "down", "lower"}
CAUSAL = re.compile(r"\b(because|caused|causes|cause|due to|driven by|drove|result of|"
                    r"resulted|led to|thanks to)\b", re.I)
# Numbers that carry no data: years, quarters, ISO dates.
ALLOWED_NUMBERS = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b|\bQ[1-4]\b")
SENTENCE = re.compile(r"[^.!?;\n]+")


def base_field(field):
    return re.sub(r"_abs(?=\[|$)", "", field)


def plain_text_ok(text, hidden_terms):
    """For model text shown without placeholders (planner assumptions): no typed
    numbers except years, quarters and ISO dates, and nothing hidden."""
    low = text.lower()
    return (not re.search(r"\d", ALLOWED_NUMBERS.sub("", text)) and "@" not in text
            and not any(t.lower() in low for t in hidden_terms))


def verify(draft, table, results, role, hidden_terms):
    """draft: WriterDraft. table: {(rid, field): Fact}. results: this request's ResultSets."""
    problems = []
    text = draft.answer

    # 1. placeholders exist
    for m in PLACEHOLDER.finditer(text):
        rid, field = key_of(m)
        if rid not in results:
            problems.append(f"{m.group(0)}: there is no result {rid}")
        elif (rid, base_field(field)) not in table:
            fields = sorted(f for r, f in table if r == rid)
            problems.append(f"{m.group(0)}: {rid} has no field {field!r}. "
                            f"Fields: {', '.join(fields)}")
    if "{{" in PLACEHOLDER.sub("", text) or "}}" in PLACEHOLDER.sub("", text):
        problems.append("a placeholder is malformed; use {{r1.field}}")

    # 2. no raw numbers
    bare = ALLOWED_NUMBERS.sub("", PLACEHOLDER.sub("", text))
    if re.search(r"\d", bare):
        problems.append("the text contains a typed number; every number must be a placeholder")

    # 3. direction words match the data, sentence by sentence
    changes = {k: f for k, f in table.items() if f.sign is not None}
    signs = {f.sign for f in changes.values()}
    # Placeholders contain a dot ({{r1.change}}); hide it, or the sentence split cuts them.
    masked = PLACEHOLDER.sub(lambda m: m.group(0).replace(".", "\0"), text)
    for sentence in (s.replace("\0", ".") for s in SENTENCE.findall(masked)):
        words = set(re.findall(r"[a-z]+", PLACEHOLDER.sub(" ", sentence).lower()))
        ups, downs = words & UP, words & DOWN
        cited = []
        for m in PLACEHOLDER.finditer(sentence):
            rid, field = key_of(m)
            f = changes.get((rid, base_field(field)))
            if f is not None:
                cited.append((m.group(0), f.sign, field.split("[")[0].endswith("_abs")))
        if not cited and (ups or downs) and len(signs) == 1:
            # no change cited, but every change has one sign: the words must fit it
            cited = [("the change", next(iter(signs)), False)]
        for name, sign, absolute in cited:
            if ups and sign <= 0:
                problems.append(f"{sorted(ups)} but {name} is not an increase")
            if downs and sign >= 0:
                problems.append(f"{sorted(downs)} but {name} is not a decrease")
            if absolute and not (ups or downs):
                problems.append(f"{name} has no sign, so its sentence needs a direction word")

    # 4. results belong to this request and role
    for rid in draft.used_results:
        if rid not in results:
            problems.append(f"used_results lists {rid}, which this request did not produce")
        elif results[rid].role != role:
            problems.append(f"{rid} was produced for another role")
    cited_ids = {m.group(1) for m in PLACEHOLDER.finditer(text)}
    if not cited_ids <= set(draft.used_results):
        problems.append(f"placeholders use {sorted(cited_ids - set(draft.used_results))} "
                        "but used_results doesn't list them")

    # 5. nothing hidden
    low = text.lower()
    if "@" in text or any(t.lower() in low for t in hidden_terms):
        problems.append("the text mentions a hidden column or a hidden value")

    # 6. no causal claims (no evidence for causes in phase 5)
    m = CAUSAL.search(text)
    if m:
        problems.append(f"{m.group(0)!r} claims a cause, but no cause was measured")
    return problems
