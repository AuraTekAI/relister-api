from django.urls import path
from .views import ZipFileUploadView, ZipFileListView, ZipFileDeleteView, ZipFileDownloadView

app_name = 'zip_manager'

urlpatterns = [
    path('', ZipFileListView.as_view(), name='list'),
    path('upload/', ZipFileUploadView.as_view(), name='upload'),
    path('<int:pk>/', ZipFileDeleteView.as_view(), name='delete'),
    path('<int:pk>/download/', ZipFileDownloadView.as_view(), name='download'),
]
