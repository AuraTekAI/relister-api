from django.contrib import admin

from .models import BlogPost


@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'author_name', 'slug', 'created_at')
    search_fields = ('title', 'author_name', 'content')
    list_filter = ('created_at',)
    readonly_fields = ('slug', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
