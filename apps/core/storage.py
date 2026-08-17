"""Storage de Django respaldado en Postgres (BLOB) en vez de filesystem.

Los FileField/ImageField del proyecto (diagrama_personalizado, documento,
archivo_p12) usan este storage para que el contenido se guarde como fila en
la tabla core_archivoblob en vez de en media/. Esto evita que los archivos
queden huerfanos al mover el proyecto entre equipos: viajan con la misma
base de datos que ya se comparte.

Se implementa como Storage de Django (no como cambio de FileField a
BinaryField) para que .open()/.read()/.save(ContentFile(...))/request.FILES
sigan funcionando sin reescribir el resto del sistema.
"""
import uuid
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class PostgresBlobStorage(Storage):
    def _abrir_modelo(self):
        from .models import ArchivoBlob
        return ArchivoBlob

    def _open(self, name, mode='rb'):
        ArchivoBlob = self._abrir_modelo()
        try:
            blob = ArchivoBlob.objects.get(pk=name)
        except (ArchivoBlob.DoesNotExist, ValueError, TypeError):
            raise FileNotFoundError(name)
        return ContentFile(blob.contenido, name=blob.nombre_original)

    def _save(self, name, content):
        ArchivoBlob = self._abrir_modelo()
        content.seek(0)
        datos = content.read()
        blob = ArchivoBlob.objects.create(
            pk=uuid.uuid4(),
            nombre_original=name,
            contenido=datos,
            tamano=len(datos),
        )
        return str(blob.pk)

    def exists(self, name):
        ArchivoBlob = self._abrir_modelo()
        return ArchivoBlob.objects.filter(pk=name).exists()

    def delete(self, name):
        ArchivoBlob = self._abrir_modelo()
        ArchivoBlob.objects.filter(pk=name).delete()

    def size(self, name):
        ArchivoBlob = self._abrir_modelo()
        blob = ArchivoBlob.objects.filter(pk=name).only('tamano').first()
        return blob.tamano if blob else 0

    def url(self, name):
        from django.urls import reverse
        return reverse('servir_archivo_blob', args=[name])

    def get_available_name(self, name, max_length=None):
        # NO generar un UUID aqui: el nombre real (ej. "PAS-MLT-...-.pdf") debe
        # preservarse para que _save() lo guarde en ArchivoBlob.nombre_original
        # y la descarga (servir_archivo_blob) pueda usarlo como Content-Disposition
        # filename. La unicidad del blob la da su propia PK (uuid.uuid4() en
        # _save), no el nombre - asi que no hace falta reemplazarlo aqui.
        return name
