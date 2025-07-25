from django.contrib import admin
from .models import CustomUser, CustomUserProfile
from django.contrib.auth.admin import UserAdmin

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'username',)
    list_filter = ('email', 'username',)

@admin.register(CustomUserProfile)
class CustomUserProfileAdmin(admin.ModelAdmin):
    lisy_display = ('user', 'bio',)
    search_filter = ('user__email', 'user__username',)