from duckduckgo_search import DDGS
import wikipedia
from .os_ops import open_notepad, open_calculator, open_cmd
from assistant.models import AssistantMemory

wikipedia.set_lang("en")


# ---------- SAVE MEMORY ----------
def save_memory(user, key, value):
    AssistantMemory.objects.create(
        user=user,
        user_query=f"Remember that {key}",
        assistant_reply=f"I will remember that {key} is {value}",
        memory_key=key,
        memory_value=value
    )


# ---------- GET MEMORY ----------
def get_memory(user, key):
    mem = AssistantMemory.objects.filter(
        user=user,
        memory_key__icontains=key
    ).last()
    return mem.memory_value if mem else None


# ---------- AI SEARCH ----------
def ai_reply(query):
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, region="us-en", max_results=3)
            for r in results:
                text = r.get("body", "")
                if len(text) > 60:
                    return text
    except:
        pass

    try:
        return wikipedia.summary(query, sentences=2)
    except:
        return "Sorry, I could not find an answer."


# ---------- MAIN COMMAND ----------
def process_command(user, query):
    if not query:
        return "I didn't hear anything."

    q = query.lower().strip()

    # 🌐 WEBSITES
    if "open google" in q:
        return "__OPEN__https://www.google.com"

    if "open youtube" in q:
        return "__OPEN__https://www.youtube.com"

    # 💻 SYSTEM COMMANDS (STOP AI HERE)
    if "open notepad" in q:
        open_notepad()
        return "__SYSTEM__Opening Notepad"
    
    if "what is your name" in q:
        return "my name is Memory Assitant"

    if "open calculator" in q:
        open_calculator()
        return "__SYSTEM__Opening Calculator"

    if "open cmd" in q:
        open_cmd()
        return "__SYSTEM__Opening Command Prompt"

    # 🧠 MEMORY SAVE
    if "my name is" in q:
        name = query.split("my name is")[-1].strip()
        save_memory(user, "name", name)
        return f"Okay, I will remember your name is {name}"

    if "i like" in q:
        like = query.split("i like")[-1].strip()
        save_memory(user, "likes", like)
        return f"Got it! You like {like}"

    # 🧠 MEMORY RECALL
    if "what is my name" in q:
        name = get_memory(user, "name")
        return f"Your name is {name}" if name else "I don't know your name yet."

    if "what do i like" in q:
        like = get_memory(user, "likes")
        return f"You like {like}" if like else "I don't know what you like yet."

    # 🤖 ONLY QUESTIONS GO HERE
    return ai_reply(query)
