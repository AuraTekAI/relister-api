from rest_framework import serializers

from .models import BlogPost

EXCERPT_LENGTH = 160


class BlogPostListSerializer(serializers.ModelSerializer):
    """Lightweight, public-facing shape for the blog list/cards page."""
    excerpt = serializers.SerializerMethodField()

    class Meta:
        model = BlogPost
        fields = ['id', 'title', 'slug', 'author_name', 'excerpt', 'created_at']

    def get_excerpt(self, obj):
        text = ' '.join(obj.content.split())
        if len(text) <= EXCERPT_LENGTH:
            return text
        return f"{text[:EXCERPT_LENGTH].rstrip()}…"


class BlogPostDetailSerializer(serializers.ModelSerializer):
    """Public single-post detail shape, keyed by the slug lookup endpoint."""

    class Meta:
        model = BlogPost
        fields = ['id', 'title', 'slug', 'author_name', 'content', 'created_at', 'updated_at']


class BlogPostCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlogPost
        fields = ['title', 'author_name', 'content']
