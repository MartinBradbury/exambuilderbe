from openai import OpenAI
from django.conf import settings
import json

client = OpenAI(api_key=settings.OPEN_AI_KEY)


def generate_questions(topic, exam_board, number_of_questions):
    prompt = f"""
    You are a qualified teacher creating exam questions for the {exam_board} exam board.

    Create {number_of_questions} exam-style questions on the topic: "{topic}" for the {exam_board} specification.

    For each question:
    - Write the question clearly, and include the **total number of marks** at the end of the question in brackets like this: [3 marks]
    - Provide a detailed **mark scheme** that clearly states what the correct answer is, and how each mark is awarded.
      - For example: "They lower activation energy of reactions (1 mark)"
      - Avoid vague labels like "Point 1" or "Point 2" — instead write what a correct student answer might say and how many marks it's worth.
    - Ensure the difficulty and format align with real {exam_board} exam questions.
    - Use only content from the official {exam_board} A-level Biology specification.
    - Return only valid JSON with no commentary or extra explanation.

    Format:
    {{
        "questions": [
            {{
                "question": "Explain how enzymes function. [3 marks]",
                "total_marks": 3,
                "mark_scheme": [
                    "They lower activation energy of reactions (1 mark)",
                    "They are not used up during the reaction (1 mark)",
                    "They have a specific active site for substrates (1 mark)"
                ]
            }},
            ...
        ]
    }}
    """

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Return valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=1500
    )

    content = response.choices[0].message.content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        print("Invalid JSON from OpenAI:", content)
        raise e


def evaluate_response_with_openai(question, mark_scheme, user_answer, exam_board):
    # Append "(1 mark)" to each marking point for clarity
    mark_scheme_with_marks = [f"{p} (1 mark)" for p in mark_scheme]

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
    - Award partial marks for partially correct statements (e.g. 0.5 if applicable, or the nearest lower integer if only whole marks are allowed).
    - Be fair but do not invent marks outside the scheme.
    - If a key point is missing or incorrect, explain that in feedback.
    - Keep to the {exam_board} A-level style of marking.
    - In your feedback, list each credited point exactly as in the mark scheme, followed by "(1 mark)", and then explain briefly what was missing.

    Respond ONLY with strict valid JSON, no extra text:
    {{
      "score": <integer or float>,
      "out_of": <integer>,
      "feedback": "List each credited point with '(1 mark)' and explain briefly what was missing."
    }}

    Example output:
    {{
      "score": 2,
      "out_of": 3,
      "feedback": "Good mention of enzymes lowering activation energy (1 mark) and specific active site (1 mark). Missing point on enzymes not being used up."
    }}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a strict but fair exam marker. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )

        content = response.choices[0].message.content.strip()
        return json.loads(content)

    except Exception as e:
        print("OpenAI error:", e)
        print("Prompt content:\n", prompt)
        raise e


def get_feedback_from_openai(prompt):
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a helpful A-level biology examiner. "
                    "Respond ONLY in valid JSON with this exact structure:\n"
                    "{\n"
                    "  \"strengths\": [\"point1\", \"point2\", \"point3\"],\n"
                    "  \"improvements\": [\"point1\", \"point2\", \"point3\"]\n"
                    "}\n\n"
                    "Each array must have exactly 3 short bullet-point strings. "
                    "Do not include anything outside of the JSON object."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.6,
        max_tokens=500
    )

    raw_content = response.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw_content)
        return {
            "strengths": parsed.get("strengths", []),
            "improvements": parsed.get("improvements", [])
        }
    except json.JSONDecodeError:
        return {
            "strengths": [],
            "improvements": [],
            "raw": raw_content
        }
