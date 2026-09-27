from django.urls import path

from .views import AsiaPayCallbackView

urlpatterns = [
    path('callback/', AsiaPayCallbackView.as_view(), name='asia-pay-callback'),
]
