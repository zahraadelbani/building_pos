from django.urls import path
from . import views

urlpatterns = [
    path('', views.pos_home, name='pos_home'),
    path('order/<int:order_id>/', views.pos_home, name='pos_order'),
    path('new-order/', views.create_new_order, name='create_new_order'),

    path('unpaid-orders/', views.unpaid_orders, name='unpaid_orders'),
    path('resume-order/<int:order_id>/', views.resume_order, name='resume_order'),
    path('pay-later/<int:order_id>/', views.pay_later, name='pay_later'),
    path('pay/<int:order_id>/', views.pay_order, name='pay_order'),
    path('print/<int:order_id>/<str:copy_type>/', views.print_receipt, name='print_receipt'),
    path('refund/<int:order_id>/', views.refund_order, name='refund_order'),

]
