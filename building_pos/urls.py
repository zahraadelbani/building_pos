from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('', include('pos.urls')),  # POS is homepage
    path('admin/', admin.site.urls),
]
