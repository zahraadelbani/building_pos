from django.urls import path
from . import views
from . import printing_api

urlpatterns = [
    path('', views.pos_home, name='pos_home'),
    path('order/<int:order_id>/', views.pos_home, name='pos_order'),
    path('new-order/', views.create_new_order, name='create_new_order'),
    path('order/<int:order_id>/delete-empty/', views.delete_empty_order, name='delete_empty_order'),
    path('order/<int:order_id>/remove-item/<int:item_id>/', views.remove_order_item, name='remove_order_item'),
    path('device/', views.device_setup, name='device_setup'),
    path('reprint/', views.reprint_orders, name='reprint_orders'),

    path('products/', views.products_page, name='products'),
    path('products/create/', views.create_product, name='create_product'),
    path('products/<int:product_id>/edit/', views.edit_product, name='edit_product'),
    path('order/<int:order_id>/add-product/<int:product_id>/', views.add_product_to_order, name='add_product_to_order'),

    path('unpaid-orders/', views.unpaid_orders, name='unpaid_orders'),
    path('resume-order/<int:order_id>/', views.resume_order, name='resume_order'),
    path('pay-later/<int:order_id>/', views.pay_later, name='pay_later'),
    path('pay/<int:order_id>/', views.pay_order, name='pay_order'),
    path('print/<int:order_id>/<str:copy_type>/', views.print_receipt, name='print_receipt'),
    path('print-profit/<int:order_id>/', views.print_profit_receipt, name='print_profit_receipt'),
    path('refund/<int:order_id>/', views.refund_order, name='refund_order'),

    path('api/printing/jobs/poll/', printing_api.poll_jobs, name='printing_poll_jobs'),
    path('api/printing/jobs/<int:job_id>/ack-printed/', printing_api.ack_printed, name='printing_ack_printed'),
    path('api/printing/jobs/<int:job_id>/ack-failed/', printing_api.ack_failed, name='printing_ack_failed'),

]
