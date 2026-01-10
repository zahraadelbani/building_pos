from django.urls import path
from . import views

urlpatterns = [
    path('', views.pos_home, name='pos_home'),
]
