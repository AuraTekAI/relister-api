from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from .models import BlogPost
from .serializers import (
    BlogPostCreateSerializer,
    BlogPostDetailSerializer,
    BlogPostListSerializer,
)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_all_blog_posts(request):
    """
    Public, unauthenticated blog post list, e.g.
    GET /api/blog/posts/?limit=20&offset=0

    Query Parameters:
    - limit: Integer (default: 20, max: 100) — page size
    - offset: Integer (default: 0) — rows to skip
    """
    try:
        limit = min(int(request.GET.get('limit', 20)), 100)
        offset = max(int(request.GET.get('offset', 0)), 0)
    except ValueError:
        return JsonResponse({'error': 'limit and offset must be integers'}, status=400)

    posts = BlogPost.objects.order_by('-created_at')
    total_count = posts.count()
    page = posts[offset:offset + limit]

    serializer = BlogPostListSerializer(page, many=True)
    return JsonResponse({
        'count': total_count,
        'limit': limit,
        'offset': offset,
        'results': serializer.data,
    }, status=200)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_blog_post_by_slug(request, slug):
    """
    Public single-post lookup by slug, e.g. GET /api/blog/posts/my-first-post/
    """
    post = BlogPost.objects.filter(slug=slug).first()
    if not post:
        return JsonResponse({'error': 'Post not found'}, status=404)

    serializer = BlogPostDetailSerializer(post)
    return JsonResponse(serializer.data, status=200)


@api_view(['POST'])
@permission_classes([AllowAny])
def create_blog_post(request):
    """
    Public, unauthenticated blog post creation — no account required, just a
    display name. POST /api/blog/posts/create/ with {title, author_name, content}.
    """
    serializer = BlogPostCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return JsonResponse({'error': serializer.errors}, status=400)

    post = serializer.save()
    return JsonResponse(BlogPostDetailSerializer(post).data, status=201)
