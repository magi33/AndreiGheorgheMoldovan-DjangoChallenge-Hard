from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('people', views.people, name='people'),
    path('people/<int:pk>', views.person_detail, name='person_detail'),
    path('simulate', views.simulate, name='simulate'),
    path('fight/<int:pk>', views.fight_detail, name='fight_detail'),
    path('actions/<str:name>', views.action, name='action'),
    path('api/status', views.status, name='status'),
]
