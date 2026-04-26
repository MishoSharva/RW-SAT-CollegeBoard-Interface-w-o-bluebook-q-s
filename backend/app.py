import os
import re
import json
from flask import Flask, jsonify, send_file, request
from pypdf import PdfReader

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PDF_PATH = os.path.join(BASE_DIR, "EBB-ANS.pdf")
JSON_PATH = os.path.join(BASE_DIR, "questions.json")
SELECTOR_PATH = os.path.join(BASE_DIR, "selector.html")
QUESTION_PATH = os.path.join(BASE_DIR, "question.html")

app = Flask(__name__)

DOMAIN_MAP = {
    "craft and structure": "craft-and-structure",
    "expression of ideas": "expression-of-ideas",
    "information and ideas": "information-and-ideas",
    "standard english conventions": "standard-english-conventions",
}

SKILL_MAP = {
    "cross-text connections": "cross-text-connections",
    "text structure and purpose": "text-structure-and-purpose",
    "words in context": "words-in-context",
    "rhetorical synthesis": "rhetorical-synthesis",
    "transitions": "transitions",
    "central ideas and details": "central-ideas-and-details",
    "command of evidence": "command-of-evidence",
    "inferences": "inferences",
    "boundaries": "boundaries",
    "form, structure, and sense": "form-structure-and-sense",
}

DOMAIN_LABELS = {
    "craft-and-structure": "Craft and Structure",
    "expression-of-ideas": "Expression of Ideas",
    "information-and-ideas": "Information and Ideas",
    "standard-english-conventions": "Standard English Conventions",
}

SKILL_LABELS = {
    "cross-text-connections": "Cross-Text Connections",
    "text-structure-and-purpose": "Text Structure and Purpose",
    "words-in-context": "Words in Context",
    "rhetorical-synthesis": "Rhetorical Synthesis",
    "transitions": "Transitions",
    "central-ideas-and-details": "Central Ideas and Details",
    "command-of-evidence": "Command of Evidence",
    "inferences": "Inferences",
    "boundaries": "Boundaries",
    "form-structure-and-sense": "Form, Structure, and Sense",
}


def clean_text(s):
    if not s:
        return ""
    s = s.replace("ﬂ", "fl").replace("ﬁ", "fi")
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def normalize_key(s):
    s = clean_text(s).lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def clean_choice_text(text):
    text = clean_text(text)
    text = re.sub(r"\s*Assessment\s*SAT.*$", "", text, flags=re.S)
    text = re.sub(r"\s*Test\s*Reading and Writing.*$", "", text, flags=re.S)
    text = re.sub(r"\s*Domain\s*[A-Za-z &]+.*$", "", text, flags=re.S)
    text = re.sub(r"\s*Skill\s*[A-Za-z ,&-]+.*$", "", text, flags=re.S)
    text = re.sub(r"\s*Difficulty\s*$", "", text, flags=re.S)
    return text.strip()


def split_stem_and_prompt(stem):
    stem = clean_text(stem)

    question_starters = [
        "Based on the text,",
        "Based on the texts,",
        "Which choice",
        "What does the text",
        "What can be concluded",
        "The student wants to",
        "Which finding",
        "Which quotation",
        "What does the graph",
        "What does the table",
        "According to the text,",
    ]

    positions = []
    lower_stem = stem.lower()

    for starter in question_starters:
        idx = lower_stem.find(starter.lower())
        if idx != -1:
            positions.append(idx)

    if positions:
        cut = min(positions)
        passage = clean_text(stem[:cut])
        prompt = clean_text(stem[cut:])
        if prompt:
            return passage, prompt

    q_index = stem.rfind("?")
    if q_index != -1:
        before_q = stem[:q_index + 1]
        sentence_breaks = []

        for token in ["\n", ". ", "! "]:
            idx = before_q.rfind(token)
            if idx != -1:
                sentence_breaks.append(idx + len(token))

        if sentence_breaks:
            start = max(sentence_breaks)
            prompt = before_q[start:].strip()
            passage = before_q[:start].strip()
            if prompt:
                return passage, prompt

        return "", stem

    return "", stem


def extract_header_fields(full_text, qid):
    pattern = re.compile(
        rf"ID:\s*{re.escape(qid)}.*?Assessment\s*SAT.*?Test\s*Reading and Writing.*?Domain\s*(.*?)\s*Skill\s*(.*?)\s*Difficulty",
        re.S,
    )
    m = pattern.search(full_text)
    if not m:
        return None, None

    domain = clean_text(m.group(1))
    skill = clean_text(m.group(2))

    domain_key = normalize_key(domain)
    skill_key = normalize_key(skill)

    domain_slug = DOMAIN_MAP.get(domain_key)
    skill_slug = SKILL_MAP.get(skill_key)

    if not domain_slug or not skill_slug:
        return None, None

    return domain_slug, skill_slug


def parse_pdf():
    if not os.path.exists(PDF_PATH):
        print("PDF not found:", PDF_PATH)
        return []

    reader = PdfReader(PDF_PATH)
    full_text = "\n".join((page.extract_text() or "") for page in reader.pages)
    full_text = clean_text(full_text)

    pattern = re.compile(
        r"Question ID\s*([a-f0-9]{8})(.*?)(?=Question ID\s*[a-f0-9]{8}|$)",
        re.S,
    )
    matches = pattern.findall(full_text)

    questions = []

    for qid, chunk in matches:
        block = clean_text(chunk)

        diff_match = re.search(r"Question Difficulty:\s*([A-Za-z]+)", block, re.S)
        if not diff_match:
            continue
        difficulty = diff_match.group(1).strip()

        ans_match = re.search(r"Correct Answer:\s*([A-D])", block)
        answer = ans_match.group(1).strip() if ans_match else None
        if not answer:
            continue

        rat_match = re.search(
            r"Rationale\s*(.*?)\s*Question Difficulty:\s*[A-Za-z]+",
            block,
            re.S,
        )
        rationale = clean_text(rat_match.group(1)) if rat_match else ""

        question_part_match = re.search(
            rf"ID:\s*{re.escape(qid)}\s*(.*?)\s*ID:\s*{re.escape(qid)}\s*Answer",
            block,
            re.S,
        )
        if not question_part_match:
            continue

        question_part = clean_text(question_part_match.group(1))

        choices = re.findall(
            r"(A|B|C|D)\.\s*(.*?)(?=\n(?:A|B|C|D)\.|$)",
            question_part,
            re.S,
        )
        choices = [(letter, clean_choice_text(text)) for letter, text in choices]

        if len(choices) != 4:
            continue

        first_choice_index = question_part.find("A.")
        if first_choice_index == -1:
            continue

        stem = clean_text(question_part[:first_choice_index])
        passage, prompt = split_stem_and_prompt(stem)

        domain_slug, skill_slug = extract_header_fields(full_text, qid)
        if not domain_slug or not skill_slug:
            continue

        questions.append(
            {
                "id": qid,
                "domain": DOMAIN_LABELS[domain_slug],
                "domain_slug": domain_slug,
                "skill": SKILL_LABELS[skill_slug],
                "skill_slug": skill_slug,
                "text": passage,
                "prompt": prompt,
                "choices": choices,
                "answer": answer,
                "rationale": rationale,
                "difficulty": difficulty,
            }
        )

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)

    print("parsed:", len(questions))
    return questions


def load_questions():
    return parse_pdf()


@app.route("/")
@app.route("/selector")
def selector():
    return send_file(SELECTOR_PATH)


@app.route("/question")
def question_page():
    return send_file(QUESTION_PATH)


@app.route("/api/meta")
def meta():
    data = load_questions()

    counts = {
        "total": len(data),
        "domains": {
            "craft-and-structure": 0,
            "expression-of-ideas": 0,
            "information-and-ideas": 0,
            "standard-english-conventions": 0,
        },
        "skills": {
            "cross-text-connections": 0,
            "text-structure-and-purpose": 0,
            "words-in-context": 0,
            "rhetorical-synthesis": 0,
            "transitions": 0,
            "central-ideas-and-details": 0,
            "command-of-evidence": 0,
            "inferences": 0,
            "boundaries": 0,
            "form-structure-and-sense": 0,
        },
    }

    for q in data:
        if q["domain_slug"] in counts["domains"]:
            counts["domains"][q["domain_slug"]] += 1
        if q["skill_slug"] in counts["skills"]:
            counts["skills"][q["skill_slug"]] += 1

    return jsonify(counts)


@app.route("/api/questions")
def get_questions():
    data = load_questions()
    mode = request.args.get("mode")
    value = request.args.get("value")
    qid = request.args.get("id")

    if mode == "skill":
        data = [q for q in data if q["skill_slug"] == value]
    elif mode == "domain":
        data = [q for q in data if q["domain_slug"] == value]
    elif mode == "review":
        pass

    if qid:
        ids = [q["id"] for q in data]
        if qid in ids:
            current_index = ids.index(qid)
            return jsonify({"questions": data, "current_index": current_index})

    return jsonify({"questions": data, "current_index": 0})


if __name__ == "__main__":
    app.run(debug=True)