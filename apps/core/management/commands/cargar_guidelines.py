import csv
from django.core.management.base import BaseCommand
from apps.core.models import Guideline


DATOS = [
    {
        "id": 1,
        "necesidad": "Implementación del Nuevo Proyecto",
        "lineamiento": "Generación del Nuevo proyecto:\n\n<<project_name>>\n\nUtilizar los siguientes componentes:\n\n<<project_name>>.ear\n<<project_name>>.ejb\n<<project_name>>.web",
        "mecanismo": "Arquitectura de Referencia JEE8.\n\nPAS-EST-012 Desarrollo de Aplicaciones - JEE\n \nPAS-EST-020 Nombrado de Aplicaciones Empresariales\n\nPAS-EST-041 Implementación de la herramienta CHECKSTYLE para la comprobación de código JAVA\n\nPAS-EST-024 Documentación Java\n\nEstándar Componente Transversal ",
        "observacion": "Proyecto Base: \n\nhttps://gitlab.iess.gob.ec/arquitectura/proyectosbase/iess-gestion-proyecto-base.git",
    },
    {
        "id": 2,
        "necesidad": "Permiso de Ejecución de Proyecto",
        "lineamiento": "En relación al proceso de levantamiento de Lineamiento sobre las tecnologías inferiores a la actual existe el estandar de migración PAS-INF-025 que resalta el punto de actualizar las aplicaciones a su versión más reciente JEE8.\n\nLa aprobación de adición de nuevas caracteristicas o correcciones de errores en las tecnologías inferiores deben venir anexados por un correo de parte del Subdirector de Desarrollo y aprobado por el Subdirector de Arquitectura respectivamente.\n\nEsto permite la generación y aprobación del lineamiento respectivo.  ",
        "mecanismo": "PAS-INF-025 Lineamiento migración aplicaciones JEE7.pdf",
        "observacion": None,
    },
    {
        "id": 3,
        "necesidad": "Implementación del Proyecto Existente",
        "lineamiento": "Implementar el requerimiento utilizando el proyecto existente:\n\n<<project_name>>\n\nUtilizar los siguientes componentes:\n\n<<project_name>>.ear\n<<project_name>>.ejb\n<<project_name>>.web",
        "mecanismo": "Arquitectura de Referencia JEE8.\n\nPAS-EST-012 Desarrollo de Aplicaciones - JEE \n\nPAS-EST-024 Documentación Java\n\nPAS-EST-020 Nombrado de Aplicaciones Empresariales\n\nPAS-EST-041 Implementación de la herramienta CHECKSTYLE para la comprobación de código JAVA\n\nEstándar Componente Transversal ",
        "observacion": "Proyecto Existente: \n\n<<url_gitlab>>",
    },
    {
        "id": 4,
        "necesidad": "Proceso de Migración de Aplicaciones",
        "lineamiento": "En relación con el proceso de levantamiento de Lineamiento sobre las tecnologías diferentes o inferiores a la actual, existe el estándar de migración PAS-INF-025 que resalta el punto de actualizar las aplicaciones a su versión más reciente JEE8.\n\nPor tal motivo, Es preponderante la migración del proyecto a la última versión de tecnología estandarizado en la institución que es JEE8 para su actualización y adición de cualquier tipo de característica que el proyecto necesite.\n\nSu migración y adición de las características respectivas serán las pautas a ser evaluadas.\n\nNOTA: Una vez realiza la migración y validación respectiva, se quitará los permisos de la aplicación antigua hasta que se verifique que la actual puede suplir en su totalidad a su antecesor.",
        "mecanismo": "PAS-INF-025 Lineamiento migración aplicaciones JEE7.pdf",
        "observacion": "Proyecto a ser migrado:\n\n<<url_gitlab>>\n\nProyecto Base:\n\nhttps://gitlab.iess.gob.ec/arquitectura/proyectosbase/iess-gestion-proyecto-base.git",
    },
    {
        "id": 5,
        "necesidad": "Querys Nativos",
        "lineamiento": " No se debe usar Native Query, a menos que se necesite manipular una gran cantidad de información además tener en cuenta que, para las relaciones entre entidades 'one-to-many' (@OneToMany) y 'many-to- many' (@ManyToMany) se debe utilizar el tipo de lectura LAZY (valor por defecto). Se debe usar la claúsula 'JOIN FETCH ' de JPQL cuando se requiera cargar de forma 'EAGER' alguna colección de algún tipo de relación con el tipo de lectura 'LAZY'; hacer esto siempre y cuando el tipo de relación '@OneToOne' o '@OneToMany' no tenga el atributo 'cascade'\".",
        "mecanismo": "Arquitectura de referencia JEE8     \n \nPAS-EST-012 Desarrollo de Aplicaciones - JEE \n\nEstándar Nombrado de Aplicaciones Empresariales \n\nPAS-EST-041 Implementación de la herramienta CHECKSTYLE para la comprobación de código JAVA",
        "observacion": None,
    },
    {
        "id": 6,
        "necesidad": "Web Service",
        "lineamiento": "Para la generación de servicios web de tipo RESTful, es necesario utilizar el proyecto \"Proyecto Base Servicio Web\", que facilita la creación de los web services.\n\nAdaptar el mismo a las necesidades respectivas  si fuera el caso para cumplir con los requerimientos respectivos.\n",
        "mecanismo": "Arquitectura de referencia JEE8     \n \nPAS-EST-012 Desarrollo de Aplicaciones - JEE \n\nPAS-EST-024 Documentación Java",
        "observacion": "Proyecto Base:\n\nhttps://gitlab.iess.gob.ec/arquitectura/proyectosbase/servicio-restful-base",
    },
    {
        "id": 7,
        "necesidad": "Web Service  - SOAP",
        "lineamiento": "Para el consumo del servicios web de tipo SOAP, es necesario el uso del proyecto:\"\" \"\"Componente_Cliente_Soap\"\" para el consumo de información.\n\nAdaptar el mismo a las necesidades respectivas  si fuera el caso para cumplir con los requerimientos respectivos.\n",
        "mecanismo": "Arquitectura de referencia JEE8     \n \nPAS-EST-012 Desarrollo de Aplicaciones - JEE\n\nPAS-EST-024 Documentación Java ",
        "observacion": "Proyecto Base: \n\nhttps://gitlab.iess.gob.ec/arquitectura/iess-componente-cliente-soap",
    },
    {
        "id": 8,
        "necesidad": "Web Service  - REST",
        "lineamiento": "Para el consumo del servicios web de tipo RESTful, es necesario el uso del proyecto:\"\" \"\"Componente_Client_Restful\"\" para el consumo de información.\n\nAdaptar el mismo a las necesidades respectivas  si fuera el caso para cumplir con los requerimientos respectivos.\n",
        "mecanismo": "Arquitectura de referencia JEE8     \n \nPAS-EST-012 Desarrollo de Aplicaciones - JEE\n\nPAS-EST-024 Documentación Java\n\nPAS-EST-014 Implementación cliente RESTful de los servicios web locales ",
        "observacion": "Proyecto Base:\n\nhttps://gitlab.iess.gob.ec/arquitectura/iess-componente-cliente-restful",
    },
    {
        "id": 9,
        "necesidad": "WSO2",
        "lineamiento": "WSO2 está determinado en el IESS como bus de servicios centralizados. Sin embargo, debido a que los servicios no son creados si no consumidos de forma interna en la institución. \n\nNo se ve obligado el uso de WSO2 para centralizar sus consumos.",
        "mecanismo": "PAS-GUI-026 Guía de Implementación del WSO2\n\nPAS-EST-020 Nombrado de Aplicaciones Empresariales",
        "observacion": None,
    },
    {
        "id": 10,
        "necesidad": "WSO2",
        "lineamiento": "WSO2 está determinado en el IESS como bus de servicios centralizados. Debido a que los servicios son creados para una entidad externa y no para un consumo interno de la institución.\n\nEs obligatorio el uso de WSO2 para centralizar los consumos y aplicar las seguridades respectivas.\n\nConsideraciones de WSO2:\n\nEs necesario que se genere una aplicación independiente con el mismo nombre del proyecto para integrar las apis generadas para dicha atención\n\nTodos los servicios deben ser vinculados a una sola API\n\nLos context tiene que ser determinados en relación con los estándares respectivos\n\nConsideraciones de Entrega: \n\nPara la publicación de web services internos o externos es necesario la entrega del documento que permitan el consumo y ejemplifiquen el servicio en si.\n\nDocumento de Servicios Web (Referencia en mecanismo de Implementación)\n",
        "mecanismo": "PAS-GUI-026 Guía de Implementación del WSO2\n\nPAS-EST-025 Creación de servicios restful ext\n\nReferencia: PAS-GUI-023 Manual de Usuario WS-FirmaEC.pdf\n\nPAS-EST-020 Nombrado de Aplicaciones Empresariales rev cacg-signed-signed",
        "observacion": None,
    },
    {
        "id": 11,
        "necesidad": "Web Service",
        "lineamiento": "Es preponderante en la generación de servicios web añadir la configuración de: com.arjuna.ats.arjuna.allowMultipleLastResources. El mismo debe ser implementado en el system properties para manejar los recursos XA (eXtended Architecture) en las transacciones distribuidas y permitir la funcionalidad correcta de los servicios.",
        "mecanismo": "Arquitectura de referencia JEE8     \n \nPAS-EST-012 Desarrollo de Aplicaciones - JEE \n\nPAS-EST-024 Documentación Java",
        "observacion": None,
    },
    {
        "id": 12,
        "necesidad": "WSO2\n",
        "lineamiento": "WSO2 está determinado en el IESS como bus de servicios centralizados. Sin embargo, debido a que los servicios no son creados si no consumidos de forma interna en la institución. \n\nNo se ve obligado el uso de WSO2 para centralizar sus consumos.",
        "mecanismo": "PAS-GUI-026 Guía de Implementación del WSO2\n\nPAS-EST-020 Nombrado de Aplicaciones Empresariales",
        "observacion": None,
    },
    {
        "id": 13,
        "necesidad": "Implementación de Auditoría",
        "lineamiento": "Pistas de Auditorias identificar transacciones criticas  (Seguridad Informática)",
        "mecanismo": "PAS-EST-002 Implementación de Auditorias en Aplicaciones",
        "observacion": None,
    },
    {
        "id": 14,
        "necesidad": "Logs",
        "lineamiento": "Se recomienda utilizar los siguientes niveles de log: WARN, ERROR, FATAL, a nivel de aplicación.",
        "mecanismo": "Estándar de aplicaciones empresariales JEE8\n\nPAS-EST-024 Documentación Java",
        "observacion": None,
    },
    {
        "id": 15,
        "necesidad": "Implementación de Reportes",
        "lineamiento": "Para la necesidad de generar reportes, utilizar el estándar de reportes en JasperReports",
        "mecanismo": "PAS-EST-017 Implementación de reportes con JasperReport",
        "observacion": None,
    },
    {
        "id": 16,
        "necesidad": "Firma EC Transversal",
        "lineamiento": "El requerimiento ingresado requiere validar la legitimidad de los documentos dentro de su proceso. Para ello, es fundamental utilizar los servicios web de Firma EC Transversal, los cuales garantizarán un proceso de firmado seguro y eficiente.",
        "mecanismo": "PAS-GUI-023 Manual de Usuario WS-FirmaEC\n\nPAS-EST-018 Implementación de Firma Electronica",
        "observacion": "La generación del Hiperlink  definido en el manual de usuario viene parametrizada inicialmente de la BDD.",
    },
    {
        "id": 17,
        "necesidad": "Gestor de Usuarios Externos (Keycloak)",
        "lineamiento": "El manejo de usuarios en el aplicativo está diseñado para usuarios externos a la institución. Por ello, es necesario modificar e integrar Keycloak en el componente autorizador, permitiendo una gestión centralizada y segura de estos usuarios.",
        "mecanismo": "PAS-GUI-014 Manual de Usuario Keycloak.PDF\n\nPAS-GUI-015 GUIA IMPLEMENTACION ADAPTADOR KEYCLOAK EN JBOSS\n\n",
        "observacion": None,
    },
    {
        "id": 18,
        "necesidad": "Notificaciones por Correo",
        "lineamiento": "Para cumplir con el requerimiento, es fundamental implementar un sistema de notificación por correo electrónico que permita validar y confirmar las acciones realizadas dentro del proceso.",
        "mecanismo": "PAS-EST-021 Implementación de envío de correo electrónico mediante SMPT Relay",
        "observacion": None,
    },
    {
        "id": 19,
        "necesidad": "Auditoría WS",
        "lineamiento": "Pistas de Auditorias identificar transacciones criticas  (Seguridad Informática)",
        "mecanismo": "PAS-EST-015 Implementación de auditorías en servicios web",
        "observacion": None,
    },
    {
        "id": 20,
        "necesidad": "Implementación de Alfresco",
        "lineamiento": "El repositorio Alfresco se utilizará para el almacenamiento de los archivos. Los ejemplos de los servicios están disponibles en el proyecto base.\n\nEl repositorio del sistema Alfresco debe ser creado en base a la solicitud anexada en la hoja 'RepositorioAlfresco' al momento de pasar a calidad ",
        "mecanismo": "PAS-EST-022 Gestión de archivos con Alfresco",
        "observacion": "Proyecto Base:\n\nhttps://gitlab.iess.gob.ec/arquitectura/proyectosbase/iess-gestion-proyecto-base.git",
    },
]


class Command(BaseCommand):
    help = 'Carga los lineamientos de software base desde el CSV original'

    def handle(self, *args, **kwargs):
        creados   = 0
        omitidos  = 0

        for dato in DATOS:
            obj, created = Guideline.objects.update_or_create(
                id=dato['id'],
                defaults={
                    'necesidad':   dato['necesidad'].strip(),
                    'lineamiento': dato['lineamiento'].strip(),
                    'mecanismo':   dato['mecanismo'].strip(),
                    'observacion': dato['observacion'],
                }
            )
            if created:
                creados += 1
                self.stdout.write(self.style.SUCCESS(f'  Creado [{obj.id}] {obj.necesidad[:60]}'))
            else:
                omitidos += 1
                self.stdout.write(f'  Actualizado [{obj.id}] {obj.necesidad[:60]}')

        self.stdout.write(self.style.SUCCESS(
            f'\nListo: {creados} creados, {omitidos} actualizados. Total: {creados + omitidos} registros.'
        ))
