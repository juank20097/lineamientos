from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def ticket_url(numero_ticket):
    """URL directa al ticket en Znuny (AgentTicketZoom). Vacio si no hay numero."""
    if not numero_ticket:
        return ''
    return f'{settings.ZNUNY_URL_BASE}/index.pl?Action=AgentTicketZoom;TicketNumber={numero_ticket}'
