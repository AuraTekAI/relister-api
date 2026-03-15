from django.urls import path
from .views import (
    PlanListView,
    SubscriptionStatusView,
    CheckoutView,
    WebhookView,
    BillingPortalView,
    CancelSubscriptionView,
    UsageTrackerView,
    InvoiceListView,
    InvoiceDetailView,
    ApplyDiscountView,
    AdminInvoiceListView,
    AdminInvoiceDetailView,
    AdminMarkInvoicePaidView,
    AdminDiscountCodeListCreateView,
    AdminDiscountCodeDetailView,
    AdminInvoiceStatsView,
)

urlpatterns = [
    path('plans/', PlanListView.as_view(), name='payment-plans'),
    path('subscription/', SubscriptionStatusView.as_view(), name='payment-subscription'),
    path('checkout/', CheckoutView.as_view(), name='payment-checkout'),
    path('webhook/', WebhookView.as_view(), name='payment-webhook'),
    path('portal/', BillingPortalView.as_view(), name='payment-portal'),
    path('cancel/', CancelSubscriptionView.as_view(), name='payment-cancel'),
    # TICKET-011
    path('usage/', UsageTrackerView.as_view(), name='payment-usage'),
    # TICKET-012
    path('invoices/', InvoiceListView.as_view(), name='payment-invoices'),
    path('invoices/<int:pk>/', InvoiceDetailView.as_view(), name='payment-invoice-detail'),
    path('apply-discount/', ApplyDiscountView.as_view(), name='payment-apply-discount'),
    # TICKET-016: Admin invoice management
    path('admin/invoices/stats/', AdminInvoiceStatsView.as_view(), name='admin-invoice-stats'),
    path('admin/invoices/', AdminInvoiceListView.as_view(), name='admin-invoice-list'),
    path('admin/invoices/<int:pk>/', AdminInvoiceDetailView.as_view(), name='admin-invoice-detail'),
    path('admin/invoices/<int:pk>/mark-paid/', AdminMarkInvoicePaidView.as_view(), name='admin-invoice-mark-paid'),
    # TICKET-018: Admin discount code management
    path('admin/discount-codes/', AdminDiscountCodeListCreateView.as_view(), name='admin-discount-code-list'),
    path('admin/discount-codes/<int:pk>/', AdminDiscountCodeDetailView.as_view(), name='admin-discount-code-detail'),
]
