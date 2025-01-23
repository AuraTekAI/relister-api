
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CustomPageNumberPagination(PageNumberPagination):
    page_size_query_param = 'limit'  # items per page

class LandingPagePagination(PageNumberPagination):
    page_size_query_param = 'limit'  # items per page

    def get_paginated_response(self, data):
        return Response({
            'count': self.page.paginator.count,  # Use self.page.paginator.count
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data,
        })