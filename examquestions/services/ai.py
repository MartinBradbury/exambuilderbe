from openai import OpenAI
from django.conf import settings
from functools import lru_cache
import json


MODEL_NAME = "gpt-4.1-mini"


@lru_cache(maxsize=1)
def get_openai_client():
    return OpenAI(api_key=settings.OPEN_AI_KEY)


def _parse_json_response_content(response):
    content = response.choices[0].message.content
    if not content:
        raise ValueError("OpenAI response content was empty.")
    return json.loads(content.strip())


def _format_mark_scheme_points(mark_scheme):
    formatted_points = []
    for point in mark_scheme or []:
        point_text = str(point).strip()
        if "(1 mark)" not in point_text.lower():
            point_text = f"{point_text} (1 mark)"
        formatted_points.append(point_text)
    return formatted_points


def _create_json_chat_completion(messages, temperature, max_tokens):
    client = get_openai_client()
    return client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )


def generate_questions(topic, exam_board, number_of_questions):
    prompt = f"""
You are a qualified teacher creating exam questions for the {exam_board} exam board.

Create {number_of_questions} exam-style questions on the topic: "{topic}" for the {exam_board} specification.

For each question:
- Write the question clearly, and include the total number of marks at the end of the question in brackets like this: [3 marks]
- Make each question fully answerable from the text you return. Do not refer to any unseen method, figure, graph, table, practical setup, results, or source material.
- If the question depends on a method, experiment, or data, include a concise stem describing that method or data directly in the `question` text before asking the student to analyse, interpret, or evaluate it.
- Provide a detailed mark scheme that clearly states what the correct answer is, and how each mark is awarded.
  - For example: "They lower activation energy of reactions (1 mark)"
  - Avoid vague labels like "Point 1" or "Point 2" — instead write what a correct student answer might say and how many marks it's worth.
- Ensure the difficulty and format align with real {exam_board} exam questions.
- Use only content from the official {exam_board} A-level Biology specification.
- Return only valid JSON with no commentary or extra explanation.

Format:
{{
    "questions": [
        {{
            "question": "A student mixes amylase with starch at 25 degrees C and measures the time taken for starch to disappear using iodine. Evaluate the method and suggest one improvement. [3 marks]",
            "total_marks": 3,
            "mark_scheme": [
                "Method only tests one temperature / limited range of conditions (1 mark)",
                "Repeat trials and calculate a mean to improve reliability (1 mark)",
                "Control variables such as pH, enzyme concentration, or starch concentration (1 mark)"
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
        max_tokens=1500,
    )

    try:
        return _parse_json_response_content(response)
    except json.JSONDecodeError as e:
        content = response.choices[0].message.content or ""
        print("Invalid JSON from OpenAI:", content)
        raise e


def evaluate_response_with_openai(question, mark_scheme, user_answer, exam_board):
    mark_scheme_with_marks = _format_mark_scheme_points(mark_scheme)

    prompt = f"""
You are a qualified {exam_board} A-level Biology examiner.

Mark the student's answer using the official {exam_board} style and the provided mark scheme.

QUESTION:
"{question}"

MARK SCHEME (each point shows expected content and its marks):
{json.dumps(mark_scheme_with_marks, indent=2)}

STUDENT ANSWER:
"{user_answer}"

Marking guidance:
- Award marks wherever there is reasonable evidence of understanding, even if the wording is not perfect.
- Accept correct synonyms, equivalent terminology, or alternative valid phrasing.
- Be lenient: if the answer shows understanding of a point, award the mark.
- Award partial marks for partially correct statements if appropriate.
- Be fair but do not invent marks outside the scheme.
- If a key point is missing or incorrect, explain that in feedback.
- Keep to the {exam_board} A-level style of marking.

Respond ONLY with strict valid JSON, no extra text:
{{
  "score": <integer or float>,
  "out_of": <integer>,
  "feedback": "Brief explanation of awarded marks and what was missing."
}}
"""

    try:
        response = _create_json_chat_completion(
            messages=[
                {"role": "system", "content": "You are a strict but fair exam marker. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return _parse_json_response_content(response)

    except Exception as e:
        print("OpenAI error:", e)
        print("Prompt content:\n", prompt)
        raise e


def evaluate_batch_responses_with_openai(answer_payloads, exam_board):
    normalized_answers = []
    for index, answer in enumerate(answer_payloads, start=1):
        normalized_answers.append(
            {
                "index": index,
                "question": str(answer.get("question", "")).strip(),
                "mark_scheme": _format_mark_scheme_points(answer.get("mark_scheme") or []),
                "user_answer": str(answer.get("user_answer", "")).strip(),
            }
        )

    prompt = f"""
You are a qualified {exam_board} A-level Biology examiner.

Mark every student answer using the provided mark scheme.
Return the results in the same order as the input.

Input answers:
{json.dumps(normalized_answers, indent=2)}

Marking guidance:
- Award marks wherever there is reasonable evidence of understanding, even if wording is imperfect.
- Accept correct synonyms, equivalent terminology, or alternative valid phrasing.
- Be fair and slightly lenient, but do not invent marks outside the scheme.
- Keep `score` numeric and `out_of` as the total marks available for that answer.
- `feedback` should be concise and specific to that answer.
- `strengths` and `improvements` must each contain exactly 3 short strings covering the whole submission.

Respond ONLY with strict valid JSON in this exact format:
{{
  "results": [
    {{
      "index": 1,
      "score": 0,
      "out_of": 0,
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
        max_tokens=1400,
    )

    parsed = _parse_json_response_content(response)
    results = parsed.get("results", [])

    if len(results) != len(normalized_answers):
        raise ValueError("Batch marking response count did not match the number of submitted answers.")

    return parsed


def get_feedback_from_openai(prompt):
    response = _create_json_chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful A-level biology examiner. "
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