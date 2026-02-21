from duckduckgo_search import DDGS
import wikipedia
import re
from datetime import datetime, timedelta
from .os_ops import open_notepad, open_calculator, open_cmd
from assistant.models import AssistantMemory, DailyMemory

wikipedia.set_lang("en")


# ============================================================
# 🧠 BASIC MEMORY FUNCTIONS
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
# 🌐 AI SEARCH (Fallback Only)
# ============================================================

def ai_reply(query):
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, region="us-en", max_results=3)
            for r in results:
                text = r.get("body", "")
                if text and len(text) > 80:
                    return text
    except:
        pass

    try:
        return wikipedia.summary(query, sentences=2)
    except:
        return "Sorry, I could not find an answer."


# ============================================================
# 📅 DATE PARSER
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
        except:
            return None, None

    return None, None


# ============================================================
# 🤖 MAIN COMMAND PROCESSOR
# ============================================================

def process_command(user, query):

    if not query:
        return "I didn't hear anything."

    q = query.lower().strip()
    today = datetime.today().date()

    # ========================================================
    # 🌐 WEBSITE COMMANDS
    # ========================================================

    if "open google" in q:
        return "__OPEN__https://www.google.com"

    if "open youtube" in q:
        return "__OPEN__https://www.youtube.com"

    # ========================================================
    # 💻 SYSTEM COMMANDS
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
    # 🧠 BASIC MEMORY
    # ========================================================

    if "my name is" in q:
        name = query.lower().split("my name is")[-1].strip()
        save_memory(user, "name", name)
        return f"Okay, I will remember your name is {name}"

    if "i like" in q:
        like = query.lower().split("i like")[-1].strip()
        save_memory(user, "likes", like)
        return f"Got it! You like {like}"

    if "what is my name" in q:
        name = get_memory(user, "name")
        return f"Your name is {name}" if name else "I don't know your name yet."

    if "what do i like" in q:
        like = get_memory(user, "likes")
        return f"You like {like}" if like else "I don't know what you like yet."

    if "what is your name" in q:
        return "My name is Memory Assistant"

    # ========================================================
    # 📅 DATE BASED MEMORY
    # ========================================================

    full_date, label = parse_date_from_query(q)

    # --------------------------------------------------------
    # 🧠 SMART DATE RECALL
    # --------------------------------------------------------

    recall_keywords = [
        "what did i do",
        "what i did",
        "tell me",
        "show me",
        "my activities",
        "my day",
        "what happened",
        "what i do",
    ]

    if full_date and any(keyword in q for keyword in recall_keywords):

        memories = DailyMemory.objects.filter(user=user, date=full_date)

        if memories.exists():
            response = f"📅 {label} you:\n"
            for m in memories:
                response += f"• {m.event}\n"
            return response.strip()
        else:
            return f"I don't have any memory for {label.lower()}."

    # --------------------------------------------------------
    # 💾 SAVE DATE MEMORY
    # --------------------------------------------------------

    if full_date and not q.endswith("?"):

        cleaned = re.sub(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}\b",
            "",
            query,
            flags=re.IGNORECASE
        )

        cleaned = cleaned.replace("today", "").replace("yesterday", "")
        event = cleaned.strip()
        event = re.sub(r"^(is|i|was|were|am)\s+", "", event, flags=re.IGNORECASE)

        if event and len(event) > 3:
            DailyMemory.objects.create(
                user=user,
                date=full_date,
                event=event
            )
            return f"Saved ✅ {label}: {event}"

    # ========================================================
    # 📊 TODAY SUMMARY (SMART DETECTION)
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

        events_text = " ".join([m.event for m in memories])
        prompt = f"Summarize this person's day professionally: {events_text}"
        summary = ai_reply(prompt)

        return f"📅 Today's Summary:\n{summary}"

    # ========================================================
    # 📊 WEEKLY SUMMARY (SMART DETECTION)
    # ========================================================

    if (
        ("summary" in q and "week" in q) or
        ("report" in q and "week" in q) or
        ("summarize" in q and "week" in q) or
        ("how was my week" in q)
    ):

        week_start = today - timedelta(days=7)

        memories = DailyMemory.objects.filter(
            user=user,
            date__gte=week_start,
            date__lte=today
        )

        if not memories.exists():
            return "You have no recorded activities this week."

        events_text = " ".join([m.event for m in memories])
        prompt = f"Summarize this person's week professionally: {events_text}"
        summary = ai_reply(prompt)

        return f"📊 Weekly Summary:\n{summary}"

    # ========================================================
    # 📅 WHAT DID I DO THIS WEEK
    # ========================================================

    if "what did i do this week" in q:

        week_start = today - timedelta(days=7)

        memories = DailyMemory.objects.filter(
            user=user,
            date__gte=week_start,
            date__lte=today
        ).order_by("date")

        if not memories.exists():
            return "You have no recorded activities this week."

        response = "🗓 This week you:\n"
        for m in memories:
            response += f"• {m.date.strftime('%b %d')} - {m.event}\n"

        return response.strip()

    # ========================================================
    # 🤖 DEFAULT AI SEARCH
    # ========================================================

    return ai_reply(query)