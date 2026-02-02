from django.db import models
from django.contrib.auth.models import User

class AssistantMemory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    user_query = models.TextField()
    assistant_reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    # NEW: personal memory
    memory_key = models.CharField(max_length=100, blank=True, null=True)
    memory_value = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.user_query[:40]

from django.db import models
from django.contrib.auth.models import User

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    image = models.ImageField(upload_to="profile_pics/", default="default.png")

    def __str__(self):
        return self.user.username
