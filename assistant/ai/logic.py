import re
import time
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
    if "my name is" in q:
        name = query.lower().split("my name is")[-1].strip()
        save_memory(user, "name", name)
        return f"Okay, I will remember your name is {name} 😊"

    if "i like" in q:
        like = query.lower().split("i like")[-1].strip()
        save_memory(user, "likes", like)
        return f"Got it! I'll remember you like {like} ✅"

    if "what is my name" in q:
        name = get_memory(user, "name")
        return f"Your name is {name} 😊" if name else "I don't know your name yet. Tell me!"

    if "what do i like" in q:
        like = get_memory(user, "likes")
        return f"You like {like} ✅" if like else "I don't know what you like yet!"

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
    # CONVERSATIONAL AI FALLBACK — Gemini with chat history
    # ========================================================
    chat_history = get_chat_history(user, limit=6)
    return gemini_reply(query, chat_history=chat_history)