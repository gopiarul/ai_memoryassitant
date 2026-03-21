from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponse
from django.db.models import Count
from django.db.models.functions import TruncDate
from datetime import datetime, timedelta
import pandas as pd
import json

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .models import AssistantMemory, Profile, Reminder, Goal
from .ai.logic import process_command, detect_mood, analyze_image, analyze_pdf, check_goal_progress


# ----------------------------------------------------------------
# LOGIN
# ----------------------------------------------------------------
def login_view(request):
    error = ""
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect("dashboard")
        else:
            error = "Invalid username or password"
    return render(request, "registration/login.html", {"error": error})


# ----------------------------------------------------------------
# LOGOUT
# ----------------------------------------------------------------
def logout_view(request):
    logout(request)
    return redirect("login")


# ----------------------------------------------------------------
# REGISTER
# ----------------------------------------------------------------
def register(request):
    if request.method == "POST":
        username = request.POST["username"]
        p1 = request.POST["password1"]
        p2 = request.POST["password2"]
        if p1 != p2:
            messages.error(request, "Passwords do not match")
            return redirect("register")
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists")
            return redirect("register")
        User.objects.create_user(username=username, password=p1)
        messages.success(request, "Account created successfully")
        return redirect("/accounts/login/")
    return render(request, "registration/register.html")


# ----------------------------------------------------------------
# DASHBOARD  (messages ordered oldest first = bottom of chat)
# ----------------------------------------------------------------
@login_required
def dashboard(request):
    speak_text = ""
    if request.method == "POST":
        query = request.POST.get("query", "").strip()
        image_file = request.FILES.get("image_file")

        # PDF UPLOAD — extract and summarize
        pdf_file = request.FILES.get("pdf_file")
        if pdf_file:
            response = analyze_pdf(pdf_file)
            mood = "neutral"
            AssistantMemory.objects.create(
                user=request.user,
                user_query="📄 Summarize: " + pdf_file.name,
                assistant_reply=response,
                mood=mood
            )
            memories = AssistantMemory.objects.filter(
                user=request.user
            ).order_by("created_at")
            reminders = Reminder.objects.filter(
                user=request.user, is_done=False
            ).order_by("-created_at")[:3]
            return render(request, "dashboard.html", {
                "memories":   memories,
                "speak_text": response,
                "reminders":  reminders,
            })

        # IMAGE UPLOAD — analyze with Gemini Vision
        if image_file:
            if not query:
                query = "What is in this image?"
            response = analyze_image(image_file, query)
            mood = detect_mood(query)
            AssistantMemory.objects.create(
                user=request.user,
                user_query="📷 " + query,
                assistant_reply=response,
                mood=mood
            )
            memories = AssistantMemory.objects.filter(
                user=request.user
            ).order_by("created_at")
            reminders = Reminder.objects.filter(
                user=request.user, is_done=False
            ).order_by("-created_at")[:3]
            return render(request, "dashboard.html", {
                "memories":   memories,
                "speak_text": response,
                "reminders":  reminders,
            })

        if not query:
            return redirect("dashboard")

        response = process_command(request.user, query)
        mood = detect_mood(query)

        if response.startswith("__OPEN__"):
            AssistantMemory.objects.create(
                user=request.user,
                user_query=query,
                assistant_reply="Opening website",
                mood=mood
            )
            memories = AssistantMemory.objects.filter(
                user=request.user
            ).order_by("created_at")          # oldest first → newest at bottom
            return render(request, "dashboard.html", {
                "open_url": response.replace("__OPEN__", ""),
                "memories": memories,
            })

        if response.startswith("__SYSTEM__"):
            clean = response.replace("__SYSTEM__", "")
            AssistantMemory.objects.create(
                user=request.user,
                user_query=query,
                assistant_reply=clean,
                mood=mood
            )
            memories = AssistantMemory.objects.filter(
                user=request.user
            ).order_by("created_at")
            return render(request, "dashboard.html", {
                "memories": memories,
                "speak_text": clean
            })

        # Extract reminder if present
        reminder_text = None
        if "__REMINDER__" in response:
            parts = response.split("__REMINDER__")
            response = parts[0].strip()
            reminder_text = parts[1].strip() if len(parts) > 1 else None
            if reminder_text:
                Reminder.objects.create(user=request.user, text=reminder_text)

        AssistantMemory.objects.create(
            user=request.user,
            user_query=query,
            assistant_reply=response,
            mood=mood
        )
        speak_text = response

    # GET — oldest first so latest appears at bottom of chat
    memories = AssistantMemory.objects.filter(
        user=request.user
    ).order_by("created_at")

    # Latest reminders for sidebar card
    reminders = Reminder.objects.filter(
        user=request.user, is_done=False
    ).order_by("-created_at")[:3]

    # Active goals for sidebar
    goals = Goal.objects.filter(
        user=request.user, status="active"
    ).order_by("-created_at")[:3]

    return render(request, "dashboard.html", {
        "memories":   memories,
        "speak_text": speak_text,
        "reminders":  reminders,
        "goals":      goals,
    })


# ----------------------------------------------------------------
# VOICE COMMAND
# ----------------------------------------------------------------
@login_required
def voice_command(request):
    from .ai.voice import listen, speak as tts_speak
    query = listen()
    if not query:
        return redirect("dashboard")
    response = process_command(request.user, query)
    mood = detect_mood(query)
    if response.startswith("__SYSTEM__"):
        clean = response.replace("__SYSTEM__", "")
        AssistantMemory.objects.create(
            user=request.user, user_query=query,
            assistant_reply=clean, mood=mood
        )
        tts_speak(clean)
        return redirect("dashboard")
    if response.startswith("__OPEN__"):
        AssistantMemory.objects.create(
            user=request.user, user_query=query,
            assistant_reply="Opening website", mood=mood
        )
        return redirect("dashboard")
    AssistantMemory.objects.create(
        user=request.user, user_query=query,
        assistant_reply=response, mood=mood
    )
    tts_speak(response)
    return redirect("dashboard")


# ----------------------------------------------------------------
# HISTORY
# ----------------------------------------------------------------
@login_required
def history_view(request):
    search = request.GET.get("q", "")
    start  = request.GET.get("start")
    end    = request.GET.get("end")
    histories = AssistantMemory.objects.filter(user=request.user)
    if search:
        histories = histories.filter(user_query__icontains=search)
    if start and end:
        histories = histories.filter(created_at__date__range=[start, end])
    histories = histories.order_by("-created_at")
    return render(request, "histroy.html", {
        "histories": histories,
        "search": search
    })


@login_required
def delete_history(request, id):
    get_object_or_404(AssistantMemory, id=id, user=request.user).delete()
    return redirect("history")


@login_required
def clear_history(request):
    AssistantMemory.objects.filter(user=request.user).delete()
    return redirect("history")


@login_required
def delete_history_by_date(request):
    if request.method == "POST":
        start = request.POST.get("start")
        end   = request.POST.get("end")
        AssistantMemory.objects.filter(
            user=request.user,
            created_at__date__range=[start, end]
        ).delete()
    return redirect("history")


# ----------------------------------------------------------------
# MEMORY
# ----------------------------------------------------------------
@login_required
def memory_view(request):
    memories = AssistantMemory.objects.filter(
        user=request.user, memory_key__isnull=False
    ).order_by("-created_at")
    return render(request, "memory.html", {"memories": memories})


@login_required
def delete_memory(request, id):
    AssistantMemory.objects.filter(id=id, user=request.user).delete()
    return redirect("memory")


@login_required
def clear_memory(request):
    AssistantMemory.objects.filter(
        user=request.user, memory_key__isnull=False
    ).delete()
    return redirect("memory")


# ----------------------------------------------------------------
# PROFILE & SETTINGS
# ----------------------------------------------------------------
@login_required
def profile_view(request):
    total_commands = AssistantMemory.objects.filter(user=request.user).count()
    total_memory   = AssistantMemory.objects.filter(
        user=request.user, memory_key__isnull=False
    ).count()
    return render(request, "profile.html", {
        "user": request.user,
        "total_commands": total_commands,
        "total_memory":   total_memory
    })


@login_required
def settings_view(request):
    user = request.user
    profile, _ = Profile.objects.get_or_create(user=user)
    if request.method == "POST":
        user.username = request.POST.get("username")
        user.email    = request.POST.get("email")
        if request.FILES.get("image"):
            profile.image = request.FILES.get("image")
        user.save()
        profile.save()
        messages.success(request, "Profile updated successfully")
        return redirect("profile")
    return render(request, "settings.html")


# ----------------------------------------------------------------
# ADMIN DASHBOARD
# ----------------------------------------------------------------
@staff_member_required
def admin_dashboard(request):
    total_users    = User.objects.count()
    total_commands = AssistantMemory.objects.count()
    total_memory   = AssistantMemory.objects.filter(
        memory_key__isnull=False
    ).count()
    daily_commands = (
        AssistantMemory.objects
        .annotate(day=TruncDate("created_at"))
        .values("day").annotate(count=Count("id"))
        .order_by("day")
    )
    labels = [d["day"].strftime("%d %b") for d in daily_commands]
    data   = [d["count"] for d in daily_commands]
    return render(request, "admin_dashboard.html", {
        "total_users": total_users,
        "total_commands": total_commands,
        "total_memory": total_memory,
        "labels": labels,
        "data":   data,
    })


# ----------------------------------------------------------------
# EXPORT
# ----------------------------------------------------------------
@login_required
def export_history_csv(request):
    histories = AssistantMemory.objects.filter(
        user=request.user
    ).order_by("-created_at")
    data = [{"Date": h.created_at, "User Query": h.user_query,
             "AI Reply": h.assistant_reply} for h in histories]
    df = pd.DataFrame(data)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="chat_history.csv"'
    df.to_csv(response, index=False)
    return response


@login_required
def export_history_pdf(request):
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="chat_history.pdf"'
    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    y = height - 40
    p.setFont("Helvetica", 10)
    for h in AssistantMemory.objects.filter(
        user=request.user
    ).order_by("-created_at"):
        text = f"{h.created_at} | You: {h.user_query} | AI: {h.assistant_reply}"
        p.drawString(40, y, text[:100])
        y -= 15
        if y < 50:
            p.showPage()
            y = height - 40
    p.save()
    return response


# ----------------------------------------------------------------
# MOOD CHART
# ----------------------------------------------------------------
@login_required
def mood_chart(request):
    MOOD_EMOJIS = {
        "happy": "😊", "sad": "😔", "angry": "😠",
        "anxious": "😰", "excited": "🎉", "stressed": "😓",
        "calm": "😌", "frustrated": "😤", "grateful": "🙏",
        "neutral": "😐",
    }
    MOOD_SCORES = {
        "excited": 10, "happy": 8, "grateful": 8,
        "calm": 6, "neutral": 5,
        "anxious": 4, "frustrated": 3,
        "stressed": 3, "sad": 2, "angry": 1,
    }
    POSITIVE_MOODS = {"happy", "excited", "calm", "grateful"}

    today    = datetime.today().date()
    all_entries = AssistantMemory.objects.filter(
        user=request.user
    ).exclude(mood__isnull=True).exclude(mood="")

    total_entries  = all_entries.count()
    top = all_entries.values("mood").annotate(c=Count("mood")).order_by("-c").first()
    top_mood       = top["mood"] if top else "neutral"
    top_mood_emoji = MOOD_EMOJIS.get(top_mood, "😐")
    today_entry    = all_entries.filter(created_at__date=today).order_by("-created_at").first()
    today_mood     = today_entry.mood if today_entry else "neutral"
    today_mood_emoji = MOOD_EMOJIS.get(today_mood, "😐")
    positive_count = all_entries.filter(mood__in=POSITIVE_MOODS).count()

    line_labels, line_scores = [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_entries = all_entries.filter(created_at__date=day)
        avg = round(sum(MOOD_SCORES.get(e.mood, 5) for e in day_entries) / day_entries.count(), 1) if day_entries.exists() else None
        line_labels.append(day.strftime("%b %d"))
        line_scores.append(avg)

    mood_counts = all_entries.values("mood").annotate(c=Count("mood")).order_by("-c")
    total = sum(m["c"] for m in mood_counts) or 1
    mood_breakdown = [{"mood": m["mood"], "count": m["c"], "emoji": MOOD_EMOJIS.get(m["mood"], "😐"), "percent": round(m["c"] / total * 100)} for m in mood_counts]
    recent_with_mood = [{"user_query": e.user_query, "mood": e.mood, "emoji": MOOD_EMOJIS.get(e.mood, "😐"), "created_at": e.created_at} for e in all_entries.order_by("-created_at")[:15]]

    return render(request, "mood_chart.html", {
        "total_entries":    total_entries,
        "top_mood":         top_mood,
        "top_mood_emoji":   top_mood_emoji,
        "today_mood":       today_mood,
        "today_mood_emoji": today_mood_emoji,
        "positive_count":   positive_count,
        "line_labels":      json.dumps(line_labels),
        "line_scores":      json.dumps(line_scores),
        "mood_breakdown":   mood_breakdown,
        "donut_labels":     json.dumps([m["mood"] for m in mood_breakdown]),
        "donut_counts":     json.dumps([m["count"] for m in mood_breakdown]),
        "recent_with_mood": recent_with_mood,
    })


# ----------------------------------------------------------------
# MARK REMINDER DONE
# ----------------------------------------------------------------
@login_required
def reminder_done(request, id):
    Reminder.objects.filter(id=id, user=request.user).update(is_done=True)
    return redirect("dashboard")


# ----------------------------------------------------------------
# GOALS PAGE
# ----------------------------------------------------------------
@login_required
def goals_view(request):
    if request.method == "POST":
        action = request.POST.get("action")
        goal_id = request.POST.get("goal_id")
        if action == "add":
            title = request.POST.get("title", "").strip()
            if title:
                Goal.objects.create(user=request.user, title=title)
        elif action == "complete" and goal_id:
            Goal.objects.filter(id=goal_id, user=request.user).update(status="completed")
        elif action == "delete" and goal_id:
            Goal.objects.filter(id=goal_id, user=request.user).delete()
        elif action == "check" and goal_id:
            from django.utils import timezone
            goal = Goal.objects.filter(id=goal_id, user=request.user).first()
            if goal:
                report = check_goal_progress(request.user, goal)
                goal.progress_report = report
                goal.last_checked = timezone.now()
                goal.save()
        return redirect("goals")

    active_goals = Goal.objects.filter(user=request.user, status="active").order_by("-created_at")
    completed_goals = Goal.objects.filter(user=request.user, status="completed").order_by("-created_at")
    return render(request, "goals.html", {
        "active_goals": active_goals,
        "completed_goals": completed_goals,
    })


# ----------------------------------------------------------------
# HOME (kept for compatibility)
# ----------------------------------------------------------------
@login_required
def home(request):
    memories = AssistantMemory.objects.filter(
        user=request.user
    ).order_by("created_at")
    return render(request, "dashboard.html", {"memories": memories})