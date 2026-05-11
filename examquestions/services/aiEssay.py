from functools import lru_cache
import json

from django.conf import settings
from openai import OpenAI


MODEL_NAME = "gpt-4.1-mini"
AQA_EXAM_BOARD = "AQA"
ESSAY_TOTAL_MARKS = 25
ESSAY_QUESTION_COUNT = 1


@lru_cache(maxsize=1)
def get_openai_client():
    return OpenAI(api_key=settings.OPEN_AI_KEY)


def _parse_json_response_content(response):
    content = response.choices[0].message.content
    if not content:
        raise ValueError("OpenAI response content was empty.")
    return json.loads(content.strip())


def _create_json_chat_completion(messages, temperature, max_tokens):
    client = get_openai_client()
    return client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )


def _build_specification_reference(specification=None):
    specification = str(specification or "").strip()
    if specification:
        return f"the {AQA_EXAM_BOARD} {specification} specification"
    return f"the {AQA_EXAM_BOARD} specification"


def generate_questions(topic, number_of_questions, specification=None):
    del topic
    del number_of_questions
    specification_reference = _build_specification_reference(specification)
    prompt = f"""
You are a qualified teacher creating AQA A-level Biology essay questions.

Create exactly {ESSAY_QUESTION_COUNT} essay-style question for {specification_reference}.

Return exactly {ESSAY_QUESTION_COUNT} question in the JSON response.

Every question must be a full {ESSAY_TOTAL_MARKS}-mark AQA essay question.

For each question:
- Write the question clearly, and include the total number of marks at the end of the question exactly like this: [{ESSAY_TOTAL_MARKS} marks]
- The question must be suitable for an extended-response AQA A-level Biology essay, not a short-answer or structured question.
- Choose a title from any topic area across the whole AQA A-level Biology specification, not from a user-selected topic.
- Base the title closely on the style, wording patterns, and breadth of real previously asked AQA A-level Biology {ESSAY_TOTAL_MARKS}-mark essay questions.
- Generate a question that feels very similar to past AQA essay titles, but do not copy a known title verbatim.
- Vary the biological focus across the full specification and do not default repeatedly to enzyme-focused essays.
- Make the question broad enough to allow students to select and link relevant knowledge from across the AQA A-level Biology specification.
- Prefer classic AQA essay title patterns such as "The importance of...", "The role of...", or broad synoptic themes that connect multiple parts of the course.
- Focus on the style of real AQA Biology essays, rewarding breadth, depth, relevance, and clear biological links.
- Do not refer to any unseen figure, graph, table, practical setup, source extract, or prior context.
- Provide an indicative mark scheme with concise bullet-point content that a high-quality essay could include.
- The mark scheme should support holistic marking of a {ESSAY_TOTAL_MARKS}-mark essay, not a list of isolated 1-mark answers.
- Use only content from the official {specification_reference} for A-level Biology.
- Return only valid JSON with no commentary or extra explanation.

Format:
{{
    "questions": [
        {{
            "question": "The importance of movement across cell membranes in living organisms. [{ESSAY_TOTAL_MARKS} marks]",
            "total_marks": {ESSAY_TOTAL_MARKS},
            "mark_scheme": [
                "Credit accurate biological knowledge linked to movement across membranes in a range of contexts such as gas exchange, absorption, nerve transmission, kidney function, and photosynthesis.",
                "Reward breadth and depth of material drawn from different areas of the AQA A-level Biology specification.",
                "Strong responses explain clear biological links and maintain focus on the title throughout.",
                "Higher-level essays are logically organised, use precise terminology, and avoid irrelevant detail."
            ]
        }}
    ]
}}
"""

    response = _create_json_chat_completion(
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1800,
    )

    try:
        return _parse_json_response_content(response)
    except json.JSONDecodeError as error:
        content = response.choices[0].message.content or ""
        print("Invalid JSON from OpenAI:", content)
        raise error


def evaluate_response_with_openai(question, mark_scheme, user_answer, specification=None):
    specification_reference = _build_specification_reference(specification)

    prompt = f"""
You are a qualified {AQA_EXAM_BOARD} A-level Biology examiner marking a {ESSAY_TOTAL_MARKS}-mark essay.

Mark the student's answer holistically using the standards from {specification_reference} and the provided indicative mark scheme.

QUESTION:
"{question}"

INDICATIVE MARK SCHEME:
{json.dumps(mark_scheme or [], indent=2)}

STUDENT ANSWER:
"{user_answer}"

Marking guidance:
- This is an AQA A-level Biology {ESSAY_TOTAL_MARKS}-mark essay.
- Use the indicative content as guidance, not as a rigid checklist.
- Reward breadth, depth, relevance, accuracy, logical organisation, and clear biological links.
- Credit valid material from across the AQA A-level Biology specification when it is relevant to the essay title.
- Penalise major inaccuracies, weak relevance, repetition, or very narrow coverage.
- Keep the score between 0 and {ESSAY_TOTAL_MARKS} inclusive.
- Feedback should briefly explain why the score was awarded and what would improve the essay.
- `strengths` must contain exactly 3 short strings summarising what the student did well.
- `improvements` must contain exactly 3 short strings summarising what would improve the essay.

Respond ONLY with strict valid JSON, no extra text:
{{
  "score": <integer or float>,
  "out_of": {ESSAY_TOTAL_MARKS},
    "feedback": "Brief explanation of awarded marks and what was missing.",
    "strengths": ["point1", "point2", "point3"],
    "improvements": ["point1", "point2", "point3"]
}}
"""

    try:
        response = _create_json_chat_completion(
            messages=[
                {"role": "system", "content": "You are a strict but fair exam marker. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        parsed = _parse_json_response_content(response)
        return {
            "score": parsed.get("score", 0),
            "out_of": parsed.get("out_of", ESSAY_TOTAL_MARKS),
            "feedback": parsed.get("feedback", ""),
            "strengths": parsed.get("strengths", []),
            "improvements": parsed.get("improvements", []),
        }
    except Exception as error:
        print("OpenAI error:", error)
        print("Prompt content:\n", prompt)
        raise error


def evaluate_batch_responses_with_openai(answer_payloads, specification=None):
    normalized_answers = []
    for index, answer in enumerate(answer_payloads, start=1):
        normalized_answers.append(
            {
                "index": index,
                "question": str(answer.get("question", "")).strip(),
                "mark_scheme": answer.get("mark_scheme") or [],
                "user_answer": str(answer.get("user_answer", "")).strip(),
            }
        )

    specification_reference = _build_specification_reference(specification)

    prompt = f"""
You are a qualified {AQA_EXAM_BOARD} A-level Biology examiner marking {ESSAY_TOTAL_MARKS}-mark essays.

Mark every student answer holistically using the provided indicative mark schemes and the standards from {specification_reference}.
Return the results in the same order as the input.

Input answers:
{json.dumps(normalized_answers, indent=2)}

Marking guidance:
- Treat each answer as an AQA A-level Biology {ESSAY_TOTAL_MARKS}-mark essay.
- Use each mark scheme as indicative guidance rather than a rigid checklist.
- Reward breadth, depth, relevance, accuracy, logical organisation, and clear biological links.
- Keep `score` numeric and between 0 and {ESSAY_TOTAL_MARKS} inclusive.
- `out_of` must always be {ESSAY_TOTAL_MARKS}.
- `feedback` should be concise and specific to that answer.
- `strengths` and `improvements` must each contain exactly 3 short strings covering the whole submission.

Respond ONLY with strict valid JSON in this exact format:
{{
  "results": [
    {{
      "index": 1,
      "score": 0,
      "out_of": {ESSAY_TOTAL_MARKS},
      "feedback": "..."
    }}
  ],
  "strengths": ["point1", "point2", "point3"],
  "improvements": ["point1", "point2", "point3"]
}}
"""

    response = _create_json_chat_completion(
        messages=[
            {"role": "system", "content": "You are a strict but fair exam marker. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1600,
    )

    parsed = _parse_json_response_content(response)
    results = parsed.get("results", [])

    if len(results) != len(normalized_answers):
        raise ValueError("Batch marking response count did not match the number of submitted answers.")

    return parsed


def get_feedback_from_openai(prompt, specification=None):
    specification_reference = _build_specification_reference(specification)
    response = _create_json_chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a helpful {AQA_EXAM_BOARD} A-level Biology essay examiner working to {specification_reference}. "
                    'Respond ONLY in valid JSON with this exact structure:\n'
                    '{\n'
                    '  "strengths": ["point1", "point2", "point3"],\n'
                    '  "improvements": ["point1", "point2", "point3"]\n'
                    '}\n\n'
                    "Each array must have exactly 3 short bullet-point strings. "
                    "Do not include anything outside of the JSON object."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.6,
        max_tokens=500,
    )

    parsed = _parse_json_response_content(response)
    return {
        "strengths": parsed.get("strengths", []),
        "improvements": parsed.get("improvements", []),
    }