from django.contrib import admin
from .models import AssistantMemory

from .models import Profile

@admin.register(AssistantMemory)
class AssistantMemoryAdmin(admin.ModelAdmin):
    list_display = ("user", "user_query", "created_at")
    search_fields = ("user_query",)
    list_filter = ("created_at",)

admin.site.register(Profile)
