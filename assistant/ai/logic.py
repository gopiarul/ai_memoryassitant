import re
import time
import base64
import urllib.request
import urllib.error
import json
from datetime import datetime, timedelta
from django.conf import settings
from .os_ops import open_notepad, open_calculator, open_cmd
from assistant.models import AssistantMemory, DailyMemory


# ============================================================
# GEMINI API HELPER
# ============================================================

def wikipedia_fallback(query):
    """Search Wikipedia using search API first, then fetch summary."""
    import urllib.parse

    # Clean question words from query
    search = re.sub(
        r"^(who is|what is|tell me about|tell about|explain|describe|what are|how is)\s+",
        "", query.lower()
    ).strip()

    try:
        # Step 1: Search Wikipedia for the best page title
        encoded_search = urllib.parse.quote(search)
        search_url = (
            "https://en.wikipedia.org/w/api.php"
            "?action=query&list=search"
            f"&srsearch={encoded_search}"
            "&format=json&srlimit=3"
        )
        req = urllib.request.Request(
            search_url,
            headers={"User-Agent": "MemoryAssistant/1.0 (student project)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("query", {}).get("search", [])
            if not results:
                return "Sorry, I could not find information on that topic."
            page_title = results[0]["title"]

        # Step 2: Fetch the plain-text summary for that page
        encoded_title = urllib.parse.quote(page_title.replace(" ", "_"))
        summary_url = (
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
        )
        req2 = urllib.request.Request(
            summary_url,
            headers={"User-Agent": "MemoryAssistant/1.0 (student project)"}
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            data2 = json.loads(resp2.read().decode("utf-8"))
            extract = data2.get("extract", "")
            if extract and len(extract) > 30:
                sentences = extract.split(". ")
                return f"({page_title}) " + ". ".join(sentences[:4]).strip() + "."

    except urllib.error.HTTPError as e:
        pass
    except urllib.error.URLError:
        pass
    except Exception:
        pass

    return "Sorry, I could not find an answer. Please try rephrasing your question."


def gemini_reply(prompt, chat_history=None):
    """Call Gemini API with Wikipedia fallback on rate limit."""
    import urllib.parse

    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return wikipedia_fallback(prompt)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash-lite:generateContent?key={api_key}"
    )

    contents = []
    if chat_history:
        contents.extend(chat_history)
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    system_instruction = {
        "parts": [{
            "text": (
                "You are a helpful AI memory assistant. "
                "You help users remember things, recall events, "
                "and answer questions clearly and concisely. "
                "Keep responses short and friendly unless asked for detail."
            )
        }]
    }

    payload = json.dumps({
        "system_instruction": system_instruction,
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500}
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return (
                    data["candidates"][0]["content"]["parts"][0]["text"].strip()
                )
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(2)
                continue
            if e.code == 429:
                wiki = wikipedia_fallback(prompt)
                return f"(Wikipedia) {wiki}"
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                error_body = str(e)
            return f"Gemini error {e.code}: {error_body[:150]}"
        except Exception:
            return wikipedia_fallback(prompt)

    return wikipedia_fallback(prompt)


# ============================================================
# MOOD DETECTION
# ============================================================

MOOD_EMOJIS = {
    "happy": "😊",
    "sad": "😔",
    "angry": "😠",
    "anxious": "😰",
    "excited": "🎉",
    "stressed": "😓",
    "calm": "😌",
    "frustrated": "😤",
    "grateful": "🙏",
    "neutral": "😐",
}

def detect_mood(text):
    """
    Ask Gemini to detect the emotion in the text.
    Returns a single lowercase word like 'happy', 'sad', etc.
    """
    prompt = (
        f"Detect the primary emotion in this text: '{text}'\n"
        "Reply with ONLY one word from this list: "
        "happy, sad, angry, anxious, excited, stressed, calm, "
        "frustrated, grateful, neutral.\n"
        "Just the single word, nothing else."
    )
    mood = gemini_reply(prompt).strip().lower()
    # Sanitize — only accept known moods
    known = set(MOOD_EMOJIS.keys())
    if mood not in known:
        mood = "neutral"
    return mood


# ============================================================
# CHAT HISTORY BUILDER
# ============================================================

def get_chat_history(user, limit=6):
    """
    Fetch last `limit` exchanges for this user and format them
    as Gemini-compatible contents array.
    """
    recent = AssistantMemory.objects.filter(
        user=user
    ).order_by("-created_at")[:limit]

    history = []
    for mem in reversed(recent):
        history.append({
            "role": "user",
            "parts": [{"text": mem.user_query}]
        })
        history.append({
            "role": "model",
            "parts": [{"text": mem.assistant_reply}]
        })
    return history


# ============================================================
# BASIC MEMORY FUNCTIONS
# ============================================================

def save_memory(user, key, value):
    AssistantMemory.objects.create(
        user=user,
        user_query=f"Remember that {key}",
        assistant_reply=f"I will remember that {key} is {value}",
        memory_key=key,
        memory_value=value
    )


def get_memory(user, key):
    mem = AssistantMemory.objects.filter(
        user=user,
        memory_key__icontains=key
    ).last()
    return mem.memory_value if mem else None


# ============================================================
# DATE PARSER
# ============================================================

def parse_date_from_query(q):
    date_pattern = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}\b"
    today = datetime.today().date()
    current_year = today.year

    if "today" in q:
        return today, "Today"
    if "yesterday" in q:
        return today - timedelta(days=1), "Yesterday"

    match = re.search(date_pattern, q)
    if match:
        try:
            date_str = match.group().title()
            full_date = datetime.strptime(
                date_str + f" {current_year}", "%b %d %Y"
            ).date()
            return full_date, date_str
        except Exception:
            return None, None
    return None, None


# ============================================================
# SMART REMINDER GENERATOR
# ============================================================

def generate_reminder(user):
    """
    Look at last 7 days of DailyMemory and suggest a reminder.
    Uses Gemini if available, falls back to keyword-based reminders.
    """
    from datetime import date, timedelta
    today = date.today()
    week_ago = today - timedelta(days=7)

    recent = DailyMemory.objects.filter(
        user=user,
        date__gte=week_ago
    ).order_by("-date")[:10]

    if not recent:
        return None

    events_text = " | ".join([m.date.strftime('%b %d') + ": " + m.event for m in recent])
    latest_event = recent[0].event.lower()

    # Try Gemini first
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if api_key:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash-lite:generateContent?key=" + api_key
        )
        prompt = (
            "Based on these recent activities: " + events_text + ". "
            "Suggest ONE short actionable follow-up reminder. "
            "Max 12 words. Just the reminder text, nothing else."
        )
        payload = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 60}
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                reminder = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                reminder = reminder.strip('"').strip("'")
                if len(reminder) > 5 and "wikipedia" not in reminder.lower():
                    return reminder
        except Exception:
            pass  # fall through to keyword fallback

    # Keyword-based fallback reminders
    keyword_reminders = {
        "gym":      "Don't forget to track your gym progress today!",
        "workout":  "Rest and hydrate well after your workout.",
        "study":    "Review your notes from yesterday's study session.",
        "class":    "Revise what you learned in class today.",
        "hospital": "Follow up on the doctor's advice from your visit.",
        "medicine": "Remember to take your medicine on time.",
        "meeting":  "Follow up on action items from your meeting.",
        "project":  "Check your project progress and next steps.",
        "birthday": "Send wishes or plan something special!",
        "work":     "Review your tasks and plan for tomorrow.",
        "exam":     "Revise your notes and get enough sleep tonight.",
        "sleep":    "Maintain a consistent sleep schedule.",
        "diet":     "Stay consistent with your diet plan today.",
        "run":      "Track your running distance and pace.",
        "walk":     "Great habit! Try to walk daily.",
        "read":     "Continue reading — even 10 pages a day helps.",
        "code":     "Push your code and review what you built.",
        "cook":     "Try a new recipe or meal prep for tomorrow.",
        "pray":     "Keep up your spiritual routine.",
        "friend":   "Stay in touch — drop them a message today.",
    }

    for keyword, reminder in keyword_reminders.items():
        if keyword in latest_event:
            return reminder

    # Generic fallback
    return "Keep up the good work! Review your activities for today."


# ============================================================
# IMAGE ANALYSIS WITH GEMINI VISION
# ============================================================

def analyze_image(image_file, user_question="What is in this image?"):
    """
    Send image to Gemini Vision API and get description.
    image_file: Django InMemoryUploadedFile
    user_question: optional question about the image
    Returns: string description
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return "Gemini API key not configured."

    # Read and encode image as base64
    image_data = image_file.read()
    image_b64 = base64.b64encode(image_data).decode("utf-8")

    # Detect mime type from file name
    name = image_file.name.lower()
    if name.endswith(".png"):
        mime_type = "image/png"
    elif name.endswith(".gif"):
        mime_type = "image/gif"
    elif name.endswith(".webp"):
        mime_type = "image/webp"
    else:
        mime_type = "image/jpeg"

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash-lite:generateContent?key=" + api_key
    )

    payload = json.dumps({
        "contents": [{
            "role": "user",
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": image_b64
                    }
                },
                {
                    "text": user_question
                }
            ]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 500
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(2)
                continue
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                error_body = str(e)
            return "Image analysis failed: " + error_body[:100]
        except Exception as e:
            return "Could not analyze image: " + str(e)

    return "Image analysis failed. Please try again."


# ============================================================
# PDF ANALYSIS WITH GEMINI
# ============================================================

def analyze_pdf(pdf_file):
    """
    Extract text from PDF and summarize with Gemini.
    pdf_file: Django InMemoryUploadedFile
    Returns: summary string
    """
    import io
    try:
        import PyPDF2
    except ImportError:
        return "PyPDF2 not installed. Run: pip install PyPDF2"

    # Extract text from PDF
    try:
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_file.read()))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        text = text.strip()
    except Exception as e:
        return "Could not read PDF: " + str(e)

    if not text:
        return "Could not extract text from this PDF. It may be a scanned image PDF."

    # Truncate if too long — Gemini has token limits
    if len(text) > 3000:
        text = text[:3000] + "..."

    # Send to Gemini for summary
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return "Gemini API key not configured."

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash-lite:generateContent?key=" + api_key
    )

    prompt = (
        "Summarize this document clearly in 5-6 sentences. "
        "Include the main topic, key points, and any important conclusions.\n\n"
        "Document text:\n" + text
    )

    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5, "maxOutputTokens": 600}
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(2)
                continue
            if e.code == 429:
                # Fallback — return extracted text preview
                return "PDF Text Preview (Gemini busy):\n" + text[:500] + "..."
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                error_body = str(e)
            return "PDF analysis failed: " + error_body[:100]
        except Exception as e:
            return "Could not analyze PDF: " + str(e)

    return "PDF analysis failed. Please try again."


# ============================================================
# GOAL TRACKING
# ============================================================

def check_goal_progress(user, goal):
    """
    Check progress on a goal by scanning recent DailyMemory entries.
    Returns a progress report string.
    """
    from datetime import date, timedelta
    today = date.today()
    week_ago = today - timedelta(days=7)

    recent = DailyMemory.objects.filter(
        user=user,
        date__gte=week_ago
    ).order_by("-date")[:20]

    if not recent:
        return "No recent activities found to track this goal. Start logging your daily activities!"

    events_text = " | ".join([m.date.strftime("%b %d") + ": " + m.event for m in recent])

    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return "Gemini API key not configured."

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash-lite:generateContent?key=" + api_key
    )

    prompt = (
        "Goal: " + goal.title + "\n"
        "Recent activities (last 7 days): " + events_text + "\n\n"
        "Analyze how well this person is progressing toward their goal based on their activities. "
        "Give a short 2-3 sentence progress report. Be encouraging but honest. "
        "Include a percentage estimate of goal completion this week."
    )

    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 200}
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        # Fallback — simple keyword matching
        goal_words = goal.title.lower().split()
        matched = [m for m in recent if any(w in m.event.lower() for w in goal_words)]
        count = len(matched)
        total = len(recent)
        if count == 0:
            return f"No activities related to your goal '{goal.title}' found this week. Try to get started today!"
        return f"You worked on '{goal.title}' {count} time(s) this week. Keep it up!"


# ============================================================
# MAIN COMMAND PROCESSOR
# ============================================================

def process_command(user, query):
    if not query:
        return "I didn't hear anything."

    q = query.lower().strip()
    today = datetime.today().date()

    # ========================================================
    # WEBSITE COMMANDS
    # ========================================================
    if "open google" in q:
        return "__OPEN__https://www.google.com"
    if "open youtube" in q:
        return "__OPEN__https://www.youtube.com"
    if "open github" in q:
        return "__OPEN__https://www.github.com"

    # ========================================================
    # SYSTEM COMMANDS
    # ========================================================
    if "open notepad" in q:
        open_notepad()
        return "__SYSTEM__Opening Notepad"
    if "open calculator" in q:
        open_calculator()
        return "__SYSTEM__Opening Calculator"
    if "open cmd" in q:
        open_cmd()
        return "__SYSTEM__Opening Command Prompt"

    # ========================================================
    # CALCULATOR — solve math expressions
    # ========================================================
    calc_triggers = ["calculate", "what is", "solve", "compute", "math"]
    import re as _recalc
    math_match = _recalc.search(r'[\d\s\+\-\*\/\^\(\)\.%]+', q)
    if any(t in q for t in calc_triggers) and math_match and any(c in q for c in ['+','-','*','/','^','%']):
        expr = math_match.group().strip()
        try:
            # Safe eval — only allow math characters
            safe_expr = expr.replace('^', '**').replace('%', '/100*')
            result = eval(safe_expr, {"__builtins__": {}}, {})
            return "Calculator: " + expr + " = " + str(round(result, 6))
        except Exception:
            pass

    # ========================================================
    # DICTIONARY — meaning of any word
    # ========================================================
    dict_triggers = ["what does", "meaning of", "define ", "definition of", "what is the meaning"]
    if any(t in q for t in dict_triggers):
        word = q
        for t in dict_triggers:
            if t in word:
                word = word.split(t)[-1].strip().rstrip("?").strip()
                break
        word = word.split()[0] if word else ""
        if word:
            try:
                import urllib.parse as _up
                dict_url = "https://api.dictionaryapi.dev/api/v2/entries/en/" + _up.quote(word)
                req = urllib.request.Request(dict_url, headers={"User-Agent": "MnemoX/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    meanings = data[0].get("meanings", [])
                    if meanings:
                        part = meanings[0].get("partOfSpeech", "")
                        defs = meanings[0].get("definitions", [])
                        definition = defs[0].get("definition", "") if defs else ""
                        example = defs[0].get("example", "") if defs else ""
                        result = word.capitalize() + " (" + part + "): " + definition
                        if example:
                            result += "\nExample: " + example
                        return result
            except Exception:
                pass

    # ========================================================
    # LANGUAGE TRANSLATION
    # ========================================================
    translate_match = _recalc.match(
        r"translate\s+(.+?)\s+(?:to|in|into)\s+(\w+(?:\s\w+)?)", query, _recalc.IGNORECASE
    )
    if translate_match or q.startswith("translate "):
        if translate_match:
            text_to_translate = translate_match.group(1).strip()
            target_lang = translate_match.group(2).strip()
        else:
            parts = query.replace("translate", "").replace("Translate", "").strip()
            text_to_translate = parts
            target_lang = "Tamil"

        # Use Gemini directly for accurate translation
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if api_key:
            g_url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.0-flash-lite:generateContent?key=" + api_key
            )
            prompt = (
                "Translate the following text to " + target_lang + ". "
                "Reply with ONLY the translated text in " + target_lang + " script. "
                "Do not include any English, explanation or extra words.\n\n"
                "Text: " + text_to_translate
            )
            payload = json.dumps({
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200}
            }).encode("utf-8")
            req = urllib.request.Request(
                g_url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if result and len(result) > 0:
                        return "Translation (" + target_lang.capitalize() + "):\n" + result
            except Exception:
                pass

        return "Translation not available right now — Gemini quota exceeded. Please try again later or get a new API key."

    # ========================================================
    # JOKES & MOTIVATION
    # ========================================================
    import random as _random
    if "tell me a joke" in q or "give me a joke" in q or "joke" in q:
        jokes = [
            "Why don't programmers like nature? It has too many bugs!",
            "Why did Python break up with Java? Too many exceptions.",
            "I told my computer I needed a break — now it won't stop sending me Kit-Kat ads.",
            "How many programmers does it take to change a light bulb? None — that's a hardware problem!",
            "Why do Java developers wear glasses? Because they don't C#!",
            "A SQL query walks into a bar, walks up to two tables and asks: Can I join you?",
            "Why did the developer go broke? Because he used up all his cache!",
            "What do you call a programmer from Finland? Nerdic.",
            "How do you comfort a JavaScript bug? You console it.",
            "Why was the JavaScript developer sad? Because he didn't Node how to Express himself.",
        ]
        return _random.choice(jokes)

    if "motivate me" in q or "motivation" in q or "inspire me" in q or "give me a quote" in q or "motivational quote" in q:
        quotes = [
            "The only way to do great work is to love what you do. — Steve Jobs",
            "It does not matter how slowly you go as long as you do not stop. — Confucius",
            "Code is like humor. When you have to explain it, it's bad. — Cory House",
            "First, solve the problem. Then, write the code. — John Johnson",
            "The best time to plant a tree was 20 years ago. The second best time is now.",
            "Your limitation — it's only your imagination.",
            "Push yourself, because no one else is going to do it for you.",
            "Great things never come from comfort zones.",
            "Dream it. Wish it. Do it.",
            "Success doesn't just find you. You have to go out and get it.",
            "The harder you work for something, the greater you'll feel when you achieve it.",
            "Don't stop when you're tired. Stop when you're done.",
            "Wake up with determination. Go to bed with satisfaction.",
        ]
        return _random.choice(quotes)

    # ========================================================
    # TODO LIST
    # ========================================================
    if q.startswith("add todo:") or q.startswith("todo:") or q.startswith("add task:"):
        for prefix in ["add todo:", "todo:", "add task:"]:
            if q.startswith(prefix):
                task = query[len(prefix):].strip()
                break
        if task:
            save_memory(user, "todo_" + str(datetime.now().timestamp())[:10], task)
            return "Todo added: " + task + " ✅\nType 'show my todos' to see all tasks."
        return "Please specify a task. Example: todo: buy groceries"

    if "show my todos" in q or "my todos" in q or "my tasks" in q or "list todos" in q:
        todos = AssistantMemory.objects.filter(
            user=user,
            memory_key__startswith="todo_"
        ).order_by("-created_at")[:10]
        if not todos.exists():
            return "No todos yet! Add one by typing: todo: your task here"
        response = "Your Todo List:\n"
        for i, t in enumerate(todos, 1):
            response += "\n" + str(i) + ". " + t.memory_value
        return response

    if "clear todos" in q or "delete todos" in q:
        AssistantMemory.objects.filter(
            user=user,
            memory_key__startswith="todo_"
        ).delete()
        return "All todos cleared! ✅"

    # ========================================================
    # GOAL COMMANDS
    # ========================================================
    if q.startswith("set goal:") or q.startswith("my goal is") or q.startswith("i want to"):
        from assistant.models import Goal
        goal_text = query
        for prefix in ["set goal:", "my goal is", "i want to"]:
            if q.startswith(prefix):
                goal_text = query[len(prefix):].strip()
                break
        if goal_text and len(goal_text) > 3:
            Goal.objects.create(user=user, title=goal_text)
            return "Goal set: " + goal_text + "\n\nI will track your progress. Log daily activities and type 'check my goals' anytime!"
        return "Please specify your goal. Example: set goal: exercise 5 days a week"

    if "check my goals" in q or "my goals" in q or "goal progress" in q or "how am i doing" in q:
        from assistant.models import Goal
        from django.utils import timezone
        goals = Goal.objects.filter(user=user, status="active").order_by("-created_at")
        if not goals.exists():
            return "You have no active goals yet. Set one by typing: set goal: your goal here"
        reports = []
        for goal in goals[:3]:
            report = check_goal_progress(user, goal)
            goal.progress_report = report
            goal.last_checked = timezone.now()
            goal.save()
            reports.append("Goal: " + goal.title + "\n" + report)
        return "\n\n".join(reports)

    if "complete goal" in q or "finished goal" in q or "achieved goal" in q:
        from assistant.models import Goal
        goals = Goal.objects.filter(user=user, status="active")
        if goals.exists():
            goal = goals.last()
            goal.status = "completed"
            goal.save()
            return "Congratulations! Goal completed: " + goal.title
        return "No active goals found."

    if "list goals" in q or "show goals" in q or "all goals" in q:
        from assistant.models import Goal
        goals = Goal.objects.filter(user=user).order_by("-created_at")[:10]
        if not goals.exists():
            return "You have no goals yet. Set one by typing: set goal: your goal here"
        response = "Your Goals:\n"
        for g in goals:
            tag = "Done" if g.status == "completed" else "Active" if g.status == "active" else "Abandoned"
            response += "\n[" + tag + "] " + g.title
        return response


    # MOOD CHECK (user asking how they feel / mood history)
    # ========================================================
    if "how am i feeling" in q or "what is my mood" in q or "my mood today" in q:
        recent = AssistantMemory.objects.filter(
            user=user
        ).exclude(mood__isnull=True).exclude(mood="").order_by("-created_at")[:5]
        if not recent:
            return "I haven't detected your mood yet. Talk to me a bit more!"
        moods = [m.mood for m in recent]
        mood_summary = ", ".join(moods)
        emoji = MOOD_EMOJIS.get(moods[0], "")
        return (
            f"Based on your recent messages, you seem mostly {moods[0]} {emoji}.\n"
            f"Recent mood trend: {mood_summary}"
        )

    # ========================================================
    # BASIC MEMORY
    # ========================================================

    # --- SAVE patterns ---
    # Generic pattern: "my X is Y" — catches girlfriend, boyfriend, age, college, etc.
    import re as _re
    save_match = _re.match(
        r"my ([\w\s]+?) (?:is|are|was) (.+)", q, _re.IGNORECASE
    )
    if save_match:
        key = save_match.group(1).strip()
        value = save_match.group(2).strip()
        # Skip if key is too generic or a date/time query
        skip_keys = ["day", "question", "problem", "issue", "goal", "summary", "report"]
        if key and value and key not in skip_keys and len(key) < 30 and len(value) < 100:
            save_memory(user, key, value)
            return "Got it! I will remember your " + key + " is " + value + " ✅"

    # "remember that X" pattern
    if q.startswith("remember that") or q.startswith("remember "):
        fact = query[query.lower().find("remember") + 8:].strip()
        if fact:
            save_memory(user, "note", fact)
            return "Noted! I will remember: " + fact + " ✅"

    # "i am X" — save profession, status
    iam_match = _re.match(r"i am (?:a |an )?([\w\s]+)", q)
    if iam_match and len(q.split()) <= 6:
        value = iam_match.group(1).strip()
        save_memory(user, "profession", value)
        return "Got it! I will remember you are " + value + " ✅"

    # --- RECALL patterns ---
    # "what is my X" — recall anything
    recall_match = _re.match(r"what (?:is|are|was) my ([\w\s]+)", q)
    if recall_match:
        key = recall_match.group(1).strip()
        value = get_memory(user, key)
        if value:
            return "Your " + key + " is " + value + " 😊"
        return "I don't have that information saved yet. Tell me by saying 'my " + key + " is ...'"

    # "who is my X"
    who_match = _re.match(r"who is my ([\w\s]+)", q)
    if who_match:
        key = who_match.group(1).strip()
        value = get_memory(user, key)
        if value:
            return "Your " + key + " is " + value + " 😊"
        return "I don't know your " + key + " yet. Tell me by saying 'my " + key + " is ...'"

    if "what is your name" in q:
        return "My name is Mnemox — your AI memory assistant 🧠"

    # ========================================================
    # DATE BASED MEMORY
    # ========================================================
    full_date, label = parse_date_from_query(q)

    recall_keywords = [
        "what did i do", "what i did", "tell me", "show me",
        "my activities", "my day", "what happened", "what i do",
    ]

    if full_date and any(keyword in q for keyword in recall_keywords):
        memories = DailyMemory.objects.filter(user=user, date=full_date)
        if memories.exists():
            events_text = "\n".join([f"• {m.event}" for m in memories])
            return f"📅 {label} you:\n{events_text}"
        else:
            return f"I don't have any memory for {label.lower()}."

    if full_date and not q.endswith("?"):
        cleaned = re.sub(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}\b",
            "", query, flags=re.IGNORECASE
        )
        cleaned = cleaned.replace("today", "").replace("yesterday", "").strip()
        event = re.sub(r"^(is|i|was|were|am)\s+", "", cleaned, flags=re.IGNORECASE).strip()

        if event and len(event) > 3:
            DailyMemory.objects.create(user=user, date=full_date, event=event)
            reminder = generate_reminder(user)
            if reminder:
                reply = "✅ Saved for " + label + ": " + event + "\n\n💡 Reminder: " + reminder + "\n__REMINDER__" + reminder
                return reply
            return "✅ Saved for " + label + ": " + event

    # ========================================================
    # TODAY SUMMARY — now powered by Gemini
    # ========================================================
    if (
        ("summary" in q and "today" in q) or
        ("report" in q and "today" in q) or
        ("summarize" in q and "today" in q) or
        ("how was" in q and "today" in q)
    ):
        memories = DailyMemory.objects.filter(user=user, date=today)
        if not memories.exists():
            return "You have no recorded activities for today."
        events_text = " | ".join([m.event for m in memories])
        prompt = (
            f"Write a warm, friendly summary of this person's day in 3-4 sentences. "
            f"Activities: {events_text}"
        )
        summary = gemini_reply(prompt)
        return f"📋 Today's Summary:\n\n{summary}"

    # ========================================================
    # WEEKLY SUMMARY — now powered by Gemini
    # ========================================================
    if (
        ("summary" in q and "week" in q) or
        ("report" in q and "week" in q) or
        ("summarize" in q and "week" in q) or
        ("how was my week" in q)
    ):
        week_start = today - timedelta(days=7)
        memories = DailyMemory.objects.filter(
            user=user, date__gte=week_start, date__lte=today
        )
        if not memories.exists():
            return "You have no recorded activities this week."
        events_text = " | ".join([f"{m.date.strftime('%b %d')}: {m.event}" for m in memories])
        prompt = (
            f"Write a warm, encouraging weekly review in 4-5 sentences for someone "
            f"whose week included: {events_text}"
        )
        summary = gemini_reply(prompt)
        return f"📊 Weekly Summary:\n\n{summary}"

    # ========================================================
    # WHAT DID I DO THIS WEEK
    # ========================================================
    if "what did i do this week" in q:
        week_start = today - timedelta(days=7)
        memories = DailyMemory.objects.filter(
            user=user, date__gte=week_start, date__lte=today
        ).order_by("date")
        if not memories.exists():
            return "You have no recorded activities this week."
        response = "📅 This week you:\n"
        for m in memories:
            response += f"\n• {m.date.strftime('%b %d')} — {m.event}"
        return response.strip()

    # ========================================================
    # URL / WEBSITE ANALYZER
    # ========================================================
    url_patterns = [
        "analyze:", "summarize:", "read:", "what is this link:",
        "check this url:", "analyze this:"
    ]
    url_trigger = None
    for pattern in url_patterns:
        if q.startswith(pattern):
            url_trigger = pattern
            break

    # Also detect if message contains a plain URL
    url_in_msg = None
    import re as _re2
    url_found = _re2.search(r'https?://[^\s]+', query)
    if url_found and not url_trigger:
        url_in_msg = url_found.group()

    if url_trigger or url_in_msg:
        if url_trigger:
            url = query[len(url_trigger):].strip()
        else:
            url = url_in_msg

        if not url.startswith("http"):
            url = "https://" + url

        # Fetch the webpage
        try:
            import urllib.parse as _uparse
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; MnemoX/1.0)",
                    "Accept": "text/html"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")

            # Strip HTML tags to get plain text
            clean = _re2.sub(r'<script[^>]*>.*?</script>', '', raw, flags=_re2.DOTALL)
            clean = _re2.sub(r'<style[^>]*>.*?</style>', '', clean, flags=_re2.DOTALL)
            clean = _re2.sub(r'<[^>]+>', ' ', clean)
            clean = _re2.sub(r'\s+', ' ', clean).strip()

            # Limit text length
            text_preview = clean[:3000]

            if not text_preview:
                return "Could not extract text from this URL. The page may require login."

            # Send to Gemini directly — no Wikipedia fallback
            api_key = getattr(settings, "GEMINI_API_KEY", "")
            summary = None
            if api_key:
                g_url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-2.0-flash-lite:generateContent?key=" + api_key
                )
                prompt = (
                    "Summarize this webpage in 4-5 clear sentences. "
                    "Include the main topic and key points.\n\n"
                    "URL: " + url + "\n\nContent: " + text_preview
                )
                payload = json.dumps({
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.5, "maxOutputTokens": 400}
                }).encode("utf-8")
                req2 = urllib.request.Request(
                    g_url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req2, timeout=12) as r2:
                        d = json.loads(r2.read().decode("utf-8"))
                        summary = d["candidates"][0]["content"]["parts"][0]["text"].strip()
                except Exception:
                    pass

            if summary:
                return "Summary of " + url + ":\n\n" + summary
            else:
                # Fallback — show first 500 chars of extracted text
                preview = clean[:500].strip()
                return "Page content from " + url + ":\n\n" + preview + "\n\n(Gemini busy — showing raw text preview)"

        except urllib.error.URLError:
            return "Could not reach that URL. Please check if the link is correct and accessible."
        except Exception as e:
            return "Could not analyze this URL: " + str(e)[:80]

    # ========================================================
    # NEWS — latest headlines
    # ========================================================
    if "news" in q or "latest news" in q or "headlines" in q or "top news" in q:
        try:
            import urllib.parse as _unp
            # Use GNews RSS feed — free, no key needed
            news_url = "https://news.google.com/rss/search?q=top+news+india&hl=en-IN&gl=IN&ceid=IN:en"
            req = urllib.request.Request(
                news_url, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            import re as _renews
            titles = _renews.findall(r'<title><!\[CDATA\[(.+?)\]\]></title>', raw)
            if not titles:
                titles = _renews.findall(r'<title>(.+?)</title>', raw)
            # Skip first title (feed title)
            headlines = [t for t in titles[1:11] if "Google News" not in t]
            if headlines:
                response = "📰 Latest News Headlines:\n"
                for i, h in enumerate(headlines[:7], 1):
                    response += "\n" + str(i) + ". " + h
                return response
        except Exception:
            pass
        return "Could not fetch news right now. Check your internet connection."

    # ========================================================
    # COUNTDOWN — days until an event
    # ========================================================
    countdown_match = re.search(
        r"(?:how many days|countdown|days until|days to|days left)\s+(?:until|to|for|till)?\s*(.+)",
        q, re.IGNORECASE
    )
    if countdown_match or "days until" in q or "how many days" in q or "countdown" in q:
        # Check if there's a saved date memory for this event
        event_name = countdown_match.group(1).strip().rstrip("?") if countdown_match else ""

        # Look in DailyMemory for matching event
        if event_name:
            future = DailyMemory.objects.filter(
                user=user,
                event__icontains=event_name,
                date__gte=datetime.today().date()
            ).order_by("date").first()

            if future:
                days_left = (future.date - datetime.today().date()).days
                if days_left == 0:
                    return "Today is the day! " + future.event + " is today!"
                return "Countdown: " + str(days_left) + " days until " + future.event + " (" + future.date.strftime("%b %d, %Y") + ")"

        # Check saved memories for the event
        mem = get_memory(user, event_name) if event_name else None
        if mem:
            try:
                event_date = datetime.strptime(mem, "%Y-%m-%d").date()
                days_left = (event_date - datetime.today().date()).days
                if days_left < 0:
                    return event_name.capitalize() + " was " + str(abs(days_left)) + " days ago."
                elif days_left == 0:
                    return "Today is " + event_name + "!"
                return "Countdown: " + str(days_left) + " days until " + event_name + "!"
            except Exception:
                pass

        return (
            "I don't have a date saved for that event. "
            "Save it first by typing: 'my " + (event_name or "event") + " date is YYYY-MM-DD'"
        )

    # ========================================================
    # STUDY HELPER — explain any topic simply
    # ========================================================
    study_triggers = ["explain ", "what is ", "teach me ", "how does ", "study ", "help me understand "]
    if any(q.startswith(t) for t in study_triggers) and len(q.split()) >= 3:
        topic = query
        for t in study_triggers:
            if q.startswith(t):
                topic = query[len(t):].strip()
                break
        prompt = (
            "Explain '" + topic + "' in simple terms as if teaching a college student. "
            "Use 3-4 sentences max. Give one real-world example at the end."
        )
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if api_key:
            g_url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.0-flash-lite:generateContent?key=" + api_key
            )
            payload = json.dumps({
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.5, "maxOutputTokens": 300}
            }).encode("utf-8")
            req = urllib.request.Request(
                g_url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    return "Study: " + topic + "\n\n" + result
            except Exception:
                pass

    # ========================================================
    # QUIZ — AI asks questions
    # ========================================================
    if "quiz" in q or "test me" in q or "ask me a question" in q or "quiz me" in q:
        subject = "general knowledge"
        for word in ["about", "on", "in"]:
            if word in q:
                parts = q.split(word)
                if len(parts) > 1:
                    subject = parts[-1].strip().rstrip("?")
                    break
        prompt = (
            "Ask me ONE interesting multiple choice quiz question about " + subject + ". "
            "Format it as:\\nQuestion: ...\\nA) ...\\nB) ...\\nC) ...\\nD) ...\\nAnswer: ..."
        )
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if api_key:
            g_url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.0-flash-lite:generateContent?key=" + api_key
            )
            payload = json.dumps({
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.9, "maxOutputTokens": 300}
            }).encode("utf-8")
            req = urllib.request.Request(
                g_url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    return "Quiz Time!\n\n" + result
            except Exception:
                pass
        # Fallback quiz questions
        import random as _rq
        fallback_questions = [
            "Quiz Time!\n\nQuestion: What does HTML stand for?\nA) Hyper Text Markup Language\nB) High Tech Modern Language\nC) Hyper Transfer Markup Logic\nD) Home Tool Markup Language\nAnswer: A) Hyper Text Markup Language",
            "Quiz Time!\n\nQuestion: Which language is Django built with?\nA) JavaScript\nB) Java\nC) Python\nD) Ruby\nAnswer: C) Python",
            "Quiz Time!\n\nQuestion: What does CPU stand for?\nA) Central Process Unit\nB) Central Processing Unit\nC) Computer Personal Unit\nD) Core Processing Unit\nAnswer: B) Central Processing Unit",
            "Quiz Time!\n\nQuestion: What is the default port for Django development server?\nA) 8080\nB) 3000\nC) 8000\nD) 5000\nAnswer: C) 8000",
        ]
        return _rq.choice(fallback_questions)

    # ========================================================
    # PROGRAMMING SOLVER
    # ========================================================
    code_triggers = [
        "write code", "write a program", "code for", "program for",
        "solve this code", "fix this code", "debug this", "what is wrong with",
        "how to code", "python program", "django code", "javascript code",
        "sql query", "write function", "write a function", "create function",
        "how to implement", "implement", "algorithm for", "write algorithm",
        "solve programming", "coding question", "coding problem",
        "write python", "write javascript", "write java", "write sql",
        "reverse string", "fibonacci", "factorial", "palindrome", "prime number",
        "bubble sort", "binary search", "linked list", "find largest", "even odd",
        "sorting", "searching", "recursion", "stack", "queue", "array",
        "python code", "python function", "python program",
    ]
    if any(t in q for t in code_triggers) or (
        ("def " in query or "function " in query or "class " in query or
         "print(" in query or "SELECT" in query.upper() or
         "error" in q and ("line" in q or "code" in q)) or
        ("python" in q and any(w in q for w in ["write", "code", "program", "function", "find", "check", "sort", "search"]))
    ):
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if api_key:
            g_url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.0-flash-lite:generateContent?key=" + api_key
            )
            prompt = (
                "You are an expert programming assistant. "
                "Answer this programming question clearly with working code. "
                "Always include: explanation, code with comments, and example output.\n\n"
                "Question: " + query
            )
            payload = json.dumps({
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 800}
            }).encode("utf-8")
            req = urllib.request.Request(
                g_url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    result = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if result and "wikipedia" not in result.lower():
                        return result
            except Exception:
                pass

        # Fallback — common coding answers
        code_fallbacks = {
            "reverse": """Reverse a string in Python:

def reverse_string(s):
    return s[::-1]

# Example
print(reverse_string("hello"))  # Output: olleh

# Another way using loop:
def reverse_loop(s):
    result = ""
    for char in s:
        result = char + result
    return result""",

            "factorial": """Factorial in Python:

def factorial(n):
    if n == 0 or n == 1:
        return 1
    return n * factorial(n - 1)

# Example
print(factorial(5))  # Output: 120

# Using loop:
def factorial_loop(n):
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result""",

            "fibonacci": """Fibonacci series in Python:

def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        print(a, end=" ")
        a, b = b, a + b

# Example
fibonacci(10)  # Output: 0 1 1 2 3 5 8 13 21 34

# Return as list:
def fib_list(n):
    result = []
    a, b = 0, 1
    for _ in range(n):
        result.append(a)
        a, b = b, a + b
    return result""",

            "palindrome": """Check palindrome in Python:

def is_palindrome(s):
    s = s.lower().replace(" ", "")
    return s == s[::-1]

# Example
print(is_palindrome("racecar"))  # True
print(is_palindrome("hello"))    # False""",

            "prime": """Check prime number in Python:

def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True

# Example
print(is_prime(7))   # True
print(is_prime(10))  # False""",

            "sort": """Sort a list in Python:

# Built-in sort
numbers = [5, 2, 8, 1, 9]
numbers.sort()
print(numbers)  # [1, 2, 5, 8, 9]

# Bubble sort:
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n-i-1):
            if arr[j] > arr[j+1]:
                arr[j], arr[j+1] = arr[j+1], arr[j]
    return arr""",

            "binary search": """Binary search in Python:

def binary_search(arr, target):
    left, right = 0, len(arr) - 1
    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1

# Example
arr = [1, 3, 5, 7, 9, 11]
print(binary_search(arr, 7))  # Output: 3""",

            "linked list": """Linked List in Python:

class Node:
    def __init__(self, data):
        self.data = data
        self.next = None

class LinkedList:
    def __init__(self):
        self.head = None

    def append(self, data):
        new_node = Node(data)
        if not self.head:
            self.head = new_node
            return
        curr = self.head
        while curr.next:
            curr = curr.next
        curr.next = new_node

    def display(self):
        curr = self.head
        while curr:
            print(curr.data, end=" -> ")
            curr = curr.next
        print("None")

# Example
ll = LinkedList()
ll.append(1)
ll.append(2)
ll.append(3)
ll.display()  # 1 -> 2 -> 3 -> None""",

            "stack": """Stack implementation in Python:

class Stack:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)

    def pop(self):
        if not self.is_empty():
            return self.items.pop()

    def peek(self):
        return self.items[-1] if self.items else None

    def is_empty(self):
        return len(self.items) == 0

    def size(self):
        return len(self.items)

# Example
s = Stack()
s.push(1)
s.push(2)
s.push(3)
print(s.pop())   # 3
print(s.peek())  # 2""",

            "even odd": """Check even or odd in Python:

def check_even_odd(n):
    if n % 2 == 0:
        return str(n) + " is Even"
    else:
        return str(n) + " is Odd"

# Example
print(check_even_odd(4))   # 4 is Even
print(check_even_odd(7))   # 7 is Odd""",

            "largest": """Find largest number in Python:

def find_largest(numbers):
    return max(numbers)

# Without built-in:
def find_largest_manual(numbers):
    largest = numbers[0]
    for num in numbers:
        if num > largest:
            largest = num
    return largest

# Example
nums = [3, 7, 1, 9, 4]
print(find_largest(nums))  # 9""",
        }

        # Check keywords — any word from keyword must appear in query
        for keyword, code in code_fallbacks.items():
            keyword_words = keyword.split()
            if all(w in q for w in keyword_words):
                return code
            # Also check if any single keyword word matches
            if len(keyword_words) == 1 and keyword_words[0] in q:
                return code

        # Try partial match — check if any key word appears
        for keyword, code in code_fallbacks.items():
            for word in keyword.split():
                if len(word) > 4 and word in q:
                    return code
                    break

        return "Gemini is rate limited right now. Please try again later or ask a specific question like 'write fibonacci in python', 'reverse string python', 'binary search python'."

    # ========================================================
    # CONVERSATIONAL AI FALLBACK — Gemini with chat history
    # ========================================================
    chat_history = get_chat_history(user, limit=6)
    return gemini_reply(query, chat_history=chat_history)