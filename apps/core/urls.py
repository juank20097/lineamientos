from django.urls import path
from . import views

urlpatterns = [
    path('home/',                                        views.home_view,                    name='home'),
    path('lineamiento/validar-ticket/',                  views.validar_ticket_ajax,          name='validar_ticket'),
    path('lineamiento/crear-hijo/',                      views.crear_hijo_ajax,              name='crear_hijo'),
    path('lineamiento/crear/',                           views.crear_lineamiento_view,       name='crear_lineamiento'),
    path('lineamiento/generar/<int:detalle_id>/',        views.generar_lineamiento_view,     name='generar_lineamiento'),
    path('lineamiento/generar-bdd/<int:detalle_id>/',    views.generar_lineamiento_bdd_view, name='generar_lineamiento_bdd'),
    path('lineamiento/cargar-sql/<int:detalle_id>/',     views.cargar_sql_ajax,              name='cargar_sql'),
    path('lineamiento/diagrama/<int:detalle_id>/',       views.diagrama_personalizado_ajax,  name='diagrama_personalizado'),
    path('lineamiento/chat/<int:detalle_id>/',           views.chat_software_ajax,           name='chat_software'),
    path('lineamiento/finalizar/<int:detalle_id>/',      views.finalizar_ajax,               name='finalizar_lineamiento'),
    path('lineamiento/version/<int:detalle_id>/',        views.cargar_version_ajax,          name='cargar_version'),
    path('lineamiento/descargar/<int:lineamiento_id>/',  views.descargar_pdf_solicitud,      name='descargar_solicitud'),
    path('lineamiento/editar/<int:lineamiento_id>/',     views.editar_solicitud_view,        name='editar_solicitud'),
    path('lineamiento/detalle/<int:detalle_id>/eliminar/', views.eliminar_detalle_view,      name='eliminar_detalle'),
]
