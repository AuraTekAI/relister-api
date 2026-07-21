from django.urls import path

from .views import create_blog_post, get_all_blog_posts, get_blog_post_by_slug

urlpatterns = [
    path('posts/', get_all_blog_posts, name='get_all_blog_posts'),
    # Must be registered before posts/<slug:slug>/ — otherwise Django would
    # match "create" as a slug and this view would never be reached.
    path('posts/create/', create_blog_post, name='create_blog_post'),
    path('posts/<slug:slug>/', get_blog_post_by_slug, name='get_blog_post_by_slug'),
]
