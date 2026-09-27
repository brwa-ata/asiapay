from django.urls import include, path

urlpatterns = [
    path('api/asia-pay/', include('asiapay.urls')),
]
