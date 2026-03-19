from django.urls import path
from .views import dashboard, login_view, logout_view, register, voice_command,history_view,delete_history,clear_history,memory_view,delete_memory,clear_memory,profile_view,settings_view, clear_history,admin_dashboard,delete_history_by_date,export_history_csv,export_history_pdf,home,mood_chart

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("voice/", voice_command, name="voice"),
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("register/", register, name="register"),
    path("history/", history_view, name="history"),
    path("delete/<int:id>/", delete_history, name="delete_history"),
    path("clear-history/", clear_history, name="clear_history"),
    path("memory/", memory_view, name="memory"),
path("memory/delete/<int:id>/", delete_memory, name="delete_memory"),
path("memory/clear/", clear_memory, name="clear_memory"),
path("profile/", profile_view, name="profile"),
path("settings/", settings_view, name="settings"),
path("settings/clear-history/", clear_history, name="clear_history"),
 path("admin-dashboard/", admin_dashboard, name="admin_dashboard"),
path("history/delete-date/", delete_history_by_date, name="delete_date"),
path("export/csv/", export_history_csv, name="export_csv"),
path("export/pdf/", export_history_pdf, name="export_pdf"),
path("", home, name="home"),
path("mood/", mood_chart, name="mood_chart"),




]
