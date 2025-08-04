from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services.ai import generate_questions, evaluate_response_with_openai, get_feedback_from_openai
from .models import QuestionSession, BiologyTopic
import json
from .serializers import QuestionSessionSerializer
import logging

logger = logging.getLogger(__name__)

from pathlib import Path
import random

FALLBACK_QUESTION_PATH = Path(__file__).resolve().parent.parent / "examquestions/questions.json"


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_exam_questions(request):
    topic_id = request.data.get("topic_id")
    exam_board = request.data.get("exam_board")
    number = int(request.data.get("number_of_questions"))

    if not all([topic_id, exam_board, number]):
        return Response({"error": "Missing required fields"}, status=400)

    try:
        topic = BiologyTopic.objects.get(id=topic_id)

        # 🟡 Step 1: Load fallback questions from JSON
        with open(FALLBACK_QUESTION_PATH) as f:
            all_fallback_questions = json.load(f)

        # 🟡 Step 2: Filter fallback questions by topic
        topic_fallback = [q for q in all_fallback_questions if q["topic"].lower() == topic.topic.lower()]

        fallback_count = number // 2
        ai_count = number - fallback_count

        # Limit fallback to what's available
        fallback_selected = random.sample(topic_fallback, min(fallback_count, len(topic_fallback)))

        # 🟡 Step 3: Generate the remaining questions via OpenAI
        ai_response = generate_questions(topic.topic, exam_board, ai_count)
        ai_questions = ai_response.get("questions", [])

        combined_questions = ai_questions + fallback_selected
        random.shuffle(combined_questions)

        total_available = sum(q.get("total_marks", q.get("mark", 0)) for q in combined_questions)

        session = QuestionSession.objects.create(
            user=request.user,
            topic=topic,
            exam_board=exam_board,
            number_of_questions=number,
            total_available=total_available
        )

        return Response({
            "questions": combined_questions,
            "session_id": session.id
        }, status=200)

    except BiologyTopic.DoesNotExist:
        return Response({"error": "Invalid topic selected"}, status=400)
    except json.JSONDecodeError:
        return Response({"error": "Invalid JSON format"}, status=500)
    except Exception as e:
        return Response({"error": str(e)}, status=500)



@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_user_answer(request):
    question = request.data.get("question")
    mark_scheme = request.data.get("mark_scheme")
    user_answer = request.data.get("user_answer")
    exam_board = request.data.get("exam_board", "AQA")

    logger.info("Incoming marking request:")
    logger.info("Question: %s", question)
    logger.info("User Answer: %s", user_answer)
    logger.info("Mark Scheme: %s", mark_scheme)
    logger.info("Exam Board: %s", exam_board)
    

    if not all([question, mark_scheme, user_answer]):
        return Response({"error": "Missing one or more fields."}, status=400)

    try:
        # ✅ Pass exam_board into evaluate_response_with_openai
        result = evaluate_response_with_openai(question, mark_scheme, user_answer, exam_board)
        return Response(result, status=200)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON from OpenAI: %s", e)
        return Response({"error": "Invalid JSON returned by OpenAI"}, status=500)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return Response({"error": str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_question_session(request):
    session_id = request.data.get("session_id")
    answers = request.data.get("answers")  # List of dicts: [{question, user_answer, mark_scheme, score}, ...]
    feedback_text = request.data.get("feedback")  # Optional — if you're still sending it from frontend

    if not session_id or not answers:
        return Response({"error": "Missing session_id or answers"}, status=400)

    try:
        session = QuestionSession.objects.get(id=session_id, user=request.user)
        total_score = sum([a.get("score", 0) for a in answers])

        # Prepare AI feedback prompt
        prompt = "Give strengths and weaknesses based on these answers:\n"
        for a in answers:
            prompt += f"\nQuestion: {a['question']}\nAnswer: {a['user_answer']}\nScore: {a['score']}/{session.total_available}\n"

        # Generate and save feedback
        feedback = get_feedback_from_openai(prompt)
        session.total_score = total_score
        session.feedback = json.dumps(feedback)  # ✅ Save it here
        session.save()

        return Response({
            "message": "Session submitted",
            "score": total_score,
            "out_of": session.total_available,
            "feedback": feedback
        })

    except QuestionSession.DoesNotExist:
        return Response({"error": "Session not found"}, status=404)
    except Exception as e:
        return Response({"error": str(e)}, status=500)
    
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_user_sessions(request):
    sessions = QuestionSession.objects.filter(user=request.user).order_by('-created_at')
    serializer = QuestionSessionSerializer(sessions, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_biology_topics(request):
    topics = BiologyTopic.objects.all()
    data = [{"id": t.id, "topic": t.topic} for t in topics]
    return Response(data)