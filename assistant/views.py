from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages

from .models import AssistantMemory,Profile
from .ai.logic import process_command
from .ai.voice import listen, speak
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import AssistantMemory

import pandas as pd
from django.http import HttpResponse

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.contrib.auth.models import User

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas



# ---------------- LOGIN ----------------
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


# ---------------- DASHBOARD ----------------

# ---------------- DASHBOARD ----------------
@login_required
def dashboard(request):
    if request.method == "POST":
        query = request.POST.get("query")
        response = process_command(request.user, query)

        # 🌐 WEBSITE
        if response.startswith("__OPEN__"):
            AssistantMemory.objects.create(
                user=request.user,
                user_query=query,
                assistant_reply="Opening website"
            )
            return render(request, "dashboard.html", {
                "open_url": response.replace("__OPEN__", ""),
                "memories": AssistantMemory.objects.filter(user=request.user).order_by("id")
            })

        # 💻 SYSTEM COMMAND
        if response.startswith("__SYSTEM__"):
            clean = response.replace("__SYSTEM__", "")
            AssistantMemory.objects.create(
                user=request.user,
                user_query=query,
                assistant_reply=clean
            )
            return redirect("dashboard")

        # 🤖 AI RESPONSE
        AssistantMemory.objects.create(
            user=request.user,
            user_query=query,
            assistant_reply=response
        )

    memories = AssistantMemory.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "dashboard.html", {"memories": memories})


# ---------------- VOICE COMMAND ----------------
# ---------------- VOICE COMMAND (🔥 FIXED) ----------------
@login_required
def voice_command(request):
    query = listen()
    if not query:
        return redirect("dashboard")

    response = process_command(request.user, query)

    # 💻 SYSTEM COMMAND
    if response.startswith("__SYSTEM__"):
        clean = response.replace("__SYSTEM__", "")
        AssistantMemory.objects.create(
            user=request.user,
            user_query=query,
            assistant_reply=clean
        )
        speak(clean)
        return redirect("dashboard")

    # 🌐 WEBSITE
    if response.startswith("__OPEN__"):
        AssistantMemory.objects.create(
            user=request.user,
            user_query=query,
            assistant_reply="Opening website"
        )
        return redirect("dashboard")

    # 🤖 NORMAL AI
    AssistantMemory.objects.create(
        user=request.user,
        user_query=query,
        assistant_reply=response
    )
    speak(response)
    return redirect("dashboard")



# ---------------- LOGOUT ----------------
def logout_view(request):
    logout(request)
    return redirect("login")


# ---------------- REGISTER ----------------
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

# ---------------- HISTORY PAGE ----------------


@login_required
def history_view(request):
    search = request.GET.get("q", "")
    start = request.GET.get("start")
    end = request.GET.get("end")

    histories = AssistantMemory.objects.filter(user=request.user)

    if search:
        histories = histories.filter(user_query__icontains=search)

    if start and end:
        histories = histories.filter(
            created_at__date__range=[start, end]
        )

    histories = histories.order_by("-created_at")  # 🔥 latest first

    return render(request, "histroy.html", {
        "histories": histories,
        "search": search
    })
    



# ---------------- DELETE SINGLE HISTORY ----------------
@login_required
def delete_history(request, id):
    history = get_object_or_404(AssistantMemory, id=id, user=request.user)
    history.delete()
    return redirect("history")


# ---------------- DELETE ALL HISTORY ----------------
@login_required
def clear_history(request):
    AssistantMemory.objects.filter(user=request.user).delete()
    return redirect("history")

@login_required
def memory_view(request):
    memories = AssistantMemory.objects.filter(
        user=request.user,
        memory_key__isnull=False
    ).order_by("-created_at")

    return render(request, "memory.html", {
        "memories": memories
    })


@login_required
def delete_memory(request, id):
    AssistantMemory.objects.filter(id=id, user=request.user).delete()
    return redirect("memory")


@login_required
def clear_memory(request):
    AssistantMemory.objects.filter(
        user=request.user,
        memory_key__isnull=False
    ).delete()
    return redirect("memory")

@login_required
def profile_view(request):
    total_commands = AssistantMemory.objects.filter(user=request.user).count()
    total_memory = AssistantMemory.objects.filter(
        user=request.user,
        memory_key__isnull=False
    ).count()

    return render(request, "profile.html", {
        "user": request.user,
        "total_commands": total_commands,
        "total_memory": total_memory
    })


@login_required
def settings_view(request):
    user = request.user
    profile, created = Profile.objects.get_or_create(user=user)

    if request.method == "POST":
        user.username = request.POST.get("username")
        user.email = request.POST.get("email")

        if request.FILES.get("image"):
            profile.image = request.FILES.get("image")

        user.save()
        profile.save()
        messages.success(request, "Profile updated successfully")
        return redirect("profile")

    return render(request, "settings.html")

@login_required
def clear_history(request):
    AssistantMemory.objects.filter(user=request.user).delete()
    return redirect("settings")


@staff_member_required
def admin_dashboard(request):
    total_users = User.objects.count()
    total_commands = AssistantMemory.objects.count()
    total_memory = AssistantMemory.objects.filter(
        memory_key__isnull=False
    ).count()

    # 📊 Commands per day
    daily_commands = (
        AssistantMemory.objects
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )

    labels = [d["day"].strftime("%d %b") for d in daily_commands]
    data = [d["count"] for d in daily_commands]

    return render(request, "admin_dashboard.html", {
        "total_users": total_users,
        "total_commands": total_commands,
        "total_memory": total_memory,
        "labels": labels,
        "data": data,
    })
 
@login_required
def delete_history_by_date(request):
    if request.method == "POST":
        start = request.POST.get("start")
        end = request.POST.get("end")

        AssistantMemory.objects.filter(
            user=request.user,
            created_at__date__range=[start, end]
        ).delete()

    return redirect("history")
   


@login_required
def export_history_csv(request):
    histories = AssistantMemory.objects.filter(
        user=request.user
    ).order_by("-created_at")

    data = []
    for h in histories:
        data.append({
            "Date": h.created_at,
            "User Query": h.user_query,
            "AI Reply": h.assistant_reply
        })

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

    histories = AssistantMemory.objects.filter(
        user=request.user
    ).order_by("-created_at")

    for h in histories:
        text = f"{h.created_at} | You: {h.user_query} | AI: {h.assistant_reply}"
        p.drawString(40, y, text[:100])
        y -= 15

        if y < 50:
            p.showPage()
            y = height - 40

    p.save()
    return response


