from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Autoridad, Formalizacion


class Command(BaseCommand):
    help = (
        'Reintenta la firma automatica de Revisor/Aprobador sobre una Formalizacion '
        'ya firmada por todos los responsables cuyo PDF quedo sin esas firmas (por '
        'ejemplo, porque la Autoridad no tenia credenciales completas en su momento). '
        'Parte del PDF ya firmado (no lo regenera), estampa las firmas de Autoridad '
        'que ahora si puedan firmar, y actualiza el documento guardado en la '
        'Formalizacion. No toca Znuny: el re-adjuntado del PDF al ticket, si hace '
        'falta, se hace por fuera de este comando.'
    )

    def add_arguments(self, parser):
        parser.add_argument('formalizacion_id', type=int)

    def handle(self, *args, **options):
        from apps.core.views import (
            FIRMA_COL_X, REVISOR_FIRMA_X, REVISOR_FIRMA_Y, APROBADO_NOMBRE_Y,
            _nombre_pdf, _firmar_con_autoridad, _obtener_pdf_base_actual, _asegurar_total_paginas,
        )

        formalizacion_id = options['formalizacion_id']
        try:
            formalizacion = Formalizacion.objects.select_related('lineamiento').get(pk=formalizacion_id)
        except Formalizacion.DoesNotExist:
            raise CommandError(f'No existe Formalizacion con id={formalizacion_id}')

        if not formalizacion.formalizado:
            raise CommandError(
                'Esta Formalizacion aun tiene firmas de responsables pendientes; '
                'la firma de autoridades solo aplica sobre PDFs ya firmados por todos los responsables.'
            )

        revisor = Autoridad.objects.filter(tipo='revisor', activo=True).first()
        aprobador = Autoridad.objects.filter(tipo='aprobador', activo=True).first()
        if not (revisor and revisor.puede_firmar) and not (aprobador and aprobador.puede_firmar):
            raise CommandError(
                'Ninguna Autoridad activa (Revisor/Aprobador) tiene credenciales completas '
                '(cedula/archivo_p12/clave_p12); no hay nada que firmar.'
            )

        lin = formalizacion.lineamiento

        nombre_pdf = _nombre_pdf(lin.id_numerico, lin.ticket_principal, temporal=False)
        # Partir del documento YA firmado por los responsables, no regenerar
        # el PDF desde cero (eso produciria un PDF limpio sin ningun QR y
        # perderia la firma de "Elaborado por").
        pdf_bytes = _obtener_pdf_base_actual(formalizacion)
        total_paginas = _asegurar_total_paginas(formalizacion)

        firmo_alguna = False

        if revisor and revisor.puede_firmar:
            pdf_firmado = _firmar_con_autoridad(
                pdf_bytes, revisor, lin.pk, nombre_pdf,
                REVISOR_FIRMA_X, REVISOR_FIRMA_Y, str(total_paginas),
            )
            if pdf_firmado:
                pdf_bytes = pdf_firmado
                firmo_alguna = True
                self.stdout.write(self.style.SUCCESS(f'Firma de Revisor "{revisor.nombre_completo}" aplicada.'))
            else:
                self.stdout.write(self.style.WARNING('Firma de Revisor fallo (ver logs).'))
        else:
            self.stdout.write(self.style.WARNING('Revisor sin credenciales completas: se omite.'))

        if aprobador and aprobador.puede_firmar:
            pdf_firmado = _firmar_con_autoridad(
                pdf_bytes, aprobador, lin.pk, nombre_pdf,
                FIRMA_COL_X[0], APROBADO_NOMBRE_Y, str(total_paginas),
            )
            if pdf_firmado:
                pdf_bytes = pdf_firmado
                firmo_alguna = True
                self.stdout.write(self.style.SUCCESS(f'Firma de Aprobador "{aprobador.nombre_completo}" aplicada.'))
            else:
                self.stdout.write(self.style.WARNING('Firma de Aprobador fallo (ver logs).'))
        else:
            self.stdout.write(self.style.WARNING('Aprobador sin credenciales completas: se omite.'))

        if not firmo_alguna:
            raise CommandError('No se pudo aplicar ninguna firma de autoridad; revisar logs del WS de FirmaEC.')

        formalizacion.documento.save(nombre_pdf, ContentFile(pdf_bytes), save=True)
        self.stdout.write(self.style.SUCCESS(
            f'Documento de la Formalizacion {formalizacion.pk} actualizado localmente con las nuevas firmas.'
        ))
