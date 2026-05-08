from django.urls import path
from . import views
from . import printing_api

urlpatterns = [
    path('', views.pos_home, name='pos_home'),
    path('order/<int:order_id>/', views.pos_home, name='pos_order'),
    path('new-order/', views.create_new_order, name='create_new_order'),
    path('order/<int:order_id>/delete/', views.delete_order, name='delete_order'),
    path('order/<int:order_id>/delete-empty/', views.delete_empty_order, name='delete_empty_order'),
    path('order/<int:order_id>/remove-item/<int:item_id>/', views.remove_order_item, name='remove_order_item'),
    path('order/<int:order_id>/update-item/<int:item_id>/', views.update_order_item, name='update_order_item'),
    path('order/<int:order_id>/details/', views.update_order_details, name='update_order_details'),
    path('device/', views.device_setup, name='device_setup'),
    path('reprint/', views.reprint_orders, name='reprint_orders'),
    path('order/<int:order_id>/view/', views.view_order, name='view_order'),
    path('day/', views.day_view, name='day_view'),
    path('month/', views.month_view, name='month_view'),
    path('expenses/', views.expenses_page, name='expenses'),
    path('expenses/create/', views.create_expense, name='create_expense'),
    path('expenses/<int:expense_id>/delete/', views.delete_expense, name='delete_expense'),

    path('products/', views.products_page, name='products'),
    path('stock-movements/', views.stock_movements_page, name='stock_movements'),
    path('products/create/', views.create_product, name='create_product'),
    path('products/<int:product_id>/edit/', views.edit_product, name='edit_product'),
    path('products/<int:product_id>/delete/', views.delete_product, name='delete_product'),
    path('products/<int:product_id>/stock/', views.adjust_product_stock, name='adjust_product_stock'),
    path('products/<int:product_id>/barcode/', views.product_barcode, name='product_barcode'),
    path('order/<int:order_id>/add-product/<int:product_id>/', views.add_product_to_order, name='add_product_to_order'),
    path('order/<int:order_id>/add-custom-item/', views.add_custom_item_to_order, name='add_custom_item_to_order'),

    path('unpaid-orders/', views.unpaid_orders, name='unpaid_orders'),
    path('resume-order/<int:order_id>/', views.resume_order, name='resume_order'),
    path('pay-later/<int:order_id>/', views.pay_later, name='pay_later'),
    path('pay/<int:order_id>/', views.pay_order, name='pay_order'),
    path('mark-paid/<int:order_id>/', views.mark_order_paid, name='mark_order_paid'),
    path('print/<int:order_id>/<str:copy_type>/', views.print_receipt, name='print_receipt'),
    path('print-profit/<int:order_id>/', views.print_profit_receipt, name='print_profit_receipt'),
    path('refund/<int:order_id>/', views.refund_order, name='refund_order'),
    path('day/print-summary/', views.print_day_summary, name='print_day_summary'),
    path('day/print-receipts/', views.print_day_receipts, name='print_day_receipts'),
    path('month/print-summary/', views.print_month_summary, name='print_month_summary'),
    path('month/print-receipts/', views.print_month_receipts, name='print_month_receipts'),

    path('api/printing/jobs/poll/', printing_api.poll_jobs, name='printing_poll_jobs'),
    path('api/printing/jobs/<int:job_id>/ack-printed/', printing_api.ack_printed, name='printing_ack_printed'),
    path('api/printing/jobs/<int:job_id>/ack-failed/', printing_api.ack_failed, name='printing_ack_failed'),

]
