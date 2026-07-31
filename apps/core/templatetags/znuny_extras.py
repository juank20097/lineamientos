from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def ticket_url(numero_ticket):
    """URL directa al ticket en Znuny (AgentTicketZoom). Vacio si no hay numero."""
    if not numero_ticket:
        return ''
    return f'{settings.ZNUNY_URL_BASE}/index.pl?Action=AgentTicketZoom;TicketNumber={numero_ticket}'


@register.simple_tag(takes_context=True)
def formalizaciones_pendientes_count(context):
    """Cantidad de firmas pendientes para el usuario logueado (todas si es staff)."""
    request = context.get('request')
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return 0
    from apps.core.models import FormalizacionFirma
    qs = FormalizacionFirma.objects.filter(firmado=False)
    if not user.is_staff:
        qs = qs.filter(responsable=user)
    return qs.count()
