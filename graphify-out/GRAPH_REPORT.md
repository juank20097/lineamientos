# Graph Report - lineamientos  (2026-08-20)

## Corpus Check
- Corpus is ~39,126 words - fits in a single context window. You may not need a graph.

## Summary
- 400 nodes · 643 edges · 50 communities (22 shown, 28 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 70 edges (avg confidence: 0.94)
- Token cost: 150,000 input · 49,348 output

## Community Hubs (Navigation)
- Views & Request Handlers
- Admin & Data Models
- Znuny Ticket Integration
- Usuarios & Auth Models
- Frontend Templates (Misc)
- FirmaEC Signing & Crypto
- Postgres Blob Storage
- Formalizacion Firmas & Tags
- Lineamiento Generation Forms
- Docker & Dependencies
- Migration: Consideracion Tecnica
- Core App Config
- Usuarios App Config
- Migration: Initial Core
- Migration: Guideline
- Migration: LineamientoDetalle Alter
- Migration: LineamientoGenerado Ticket
- Migration: BDD Schema
- Migration: Diagrama Personalizado
- Migration: Formalizacion
- Migration: Remove Estado
- Migration: Guideline Options
- Migration: Lineamiento Options
- Migration: Formalizacion Fecha Cierre
- Migration: Id Lote Firma
- Migration: Total Paginas
- Migration: Autoridad
- Migration: Autoridad Archivo P12
- Migration: Id Numerico
- Migration: Fecha Reemplazo
- Migration: Chat Estado
- Migration: Tipo Transversal
- Migration: Usuarios Initial
- Migration: Usuario Roles
- Migration: Usuario Email
- Docker Entrypoint Script
- Login Template
- Prueba File

## God Nodes (most connected - your core abstractions)
1. `LineamientoDetalle` - 25 edges
2. `Formalizacion` - 14 edges
3. `FormalizacionFirma` - 14 edges
4. `Usuario` - 14 edges
5. `Autoridad` - 13 edges
6. `PostgresBlobStorage` - 13 edges
7. `firmar_formalizacion_ajax()` - 13 edges
8. `ZnunySession` - 13 edges
9. `Lineamiento` - 12 edges
10. `reautenticar()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `LineamientoDetalleInline` --uses--> `LineamientoDetalle`  [INFERRED]
  apps/core/admin.py → apps/core/models.py
- `Web Service (Django/Gunicorn)` --shares_data_with--> `Django Dependency`  [INFERRED]
  docker-compose.yml → requirements.txt
- `Web Service (Django/Gunicorn)` --shares_data_with--> `Gunicorn Dependency`  [INFERRED]
  docker-compose.yml → requirements.txt
- `LineamientoGeneradoFilaInline` --uses--> `LineamientoGeneradoFila`  [INFERRED]
  apps/core/admin.py → apps/core/models.py
- `LineamientoGeneradoInline` --uses--> `LineamientoGenerado`  [INFERRED]
  apps/core/admin.py → apps/core/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Lineamiento Generation Flow (SW/BDD/Infra)** — templates_generar_lineamiento_software_chatwidget, templates_generar_lineamiento_bdd_workspace, templates_generar_lineamiento_capacidad_workspace, templates_mis_tickets_detalles_pendientes_table [INFERRED 0.85]
- **FirmaEC Electronic Signature Flow** — templates_formalizacion_modalfirmaec, templates_formalizacion_home_modalfirmaec, templates_formalizacion_firmas_pendientes_table, templates_formalizacion_home_grupos_table [INFERRED 0.85]
- **Znuny Ticket Verify/Create/Lock Lifecycle** — templates__sidebar_verificarticketprincipal, templates_crear_lineamiento_bloqueonavegacion, templates_mis_tickets_validarnv, templates_tickets_atendidas_aplicarbloqueoacciones_rationale [INFERRED 0.75]

## Communities (50 total, 28 thin omitted)

### Community 0 - "Views & Request Handlers"
Cohesion: 0.06
Nodes (71): Lineamiento, LineamientoDetalle, _asegurar_total_paginas(), _autoformalizar_si_completo(), _buscar_formalizacion_existente(), _calcular_ids(), cargar_sql_ajax(), cargar_version_ajax() (+63 more)

### Community 1 - "Admin & Data Models"
Cohesion: 0.06
Nodes (32): AutoridadAdmin, AutoridadAdminForm, GuidelineAdmin, LineamientoAdmin, LineamientoDetalleAdmin, LineamientoDetalleInline, LineamientoGeneradoAdmin, LineamientoGeneradoFilaAdmin (+24 more)

### Community 2 - "Znuny Ticket Integration"
Cohesion: 0.11
Nodes (30): cerrar_ticket(), cerrar_ticket.py Cierra un ticket en Znuny via AgentTicketClose. Uso: python…, salida(), crear_nota(), crear_nota.py Crea una nota interna en un ticket de Znuny. Uso: python…, salida(), crear_hijo(), extraer_asunto_padre() (+22 more)

### Community 3 - "Usuarios & Auth Models"
Cohesion: 0.09
Nodes (20): AbstractUser, AdminUserCreationForm, CamposObligatoriosMixin, Meta, display, register, Agrega el campo roles_seleccionados (no persistente) que se vuelca a…, Fuerza a que nombres, apellidos y correo sean obligatorios en el admin. (+12 more)

### Community 4 - "Frontend Templates (Misc)"
Cohesion: 0.07
Nodes (32): Modal Ticket Principal (Crear Lineamiento), Sidebar Navigation Partial, verificarTicketPrincipal (JS function), Znuny Manual Fallback Rationale, Znuny Orphan-Ticket Lockout Rationale, Bloqueo de Navegacion (crear_lineamiento), Crear Lineamiento Form, Bloqueo de Navegacion (editar_solicitud) (+24 more)

### Community 5 - "FirmaEC Signing & Crypto"
Cohesion: 0.14
Nodes (23): _cifrar_aes_gcm(), cifrar_credenciales_firmaec(), _cifrar_rsa_oaep(), FirmaECError, firmar_documento_acumulativo(), firmar_documento_pdf(), generar_id_lote(), _log_llamada() (+15 more)

### Community 6 - "Postgres Blob Storage"
Cohesion: 0.13
Nodes (9): Migration, ArchivoBlob, Contenido binario de un archivo subido (diagrama, PDF de Formalizacion,…, PostgresBlobStorage, Storage de Django respaldado en Postgres (BLOB) en vez de filesystem. Los…, Sirve un archivo (diagrama, PDF de Formalizacion, certificado .p12) guardado…, servir_archivo_blob(), deconstructible (+1 more)

### Community 7 - "Formalizacion Firmas & Tags"
Cohesion: 0.12
Nodes (16): FormalizacionFirma, Firma individual de un responsable (por tipo de lineamiento) sobre una…, LineamientoGenerado exacto que esta firma respalda, segun el version_map de la…, formalizaciones_pendientes_count(), Cantidad de firmas pendientes para el usuario logueado (todas si es staff)., URL directa al ticket en Znuny (AgentTicketZoom). Vacio si no hay numero., ticket_url(), _agrupar_firmas_por_ticket() (+8 more)

### Community 8 - "Lineamiento Generation Forms"
Cohesion: 0.15
Nodes (16): actualizarSQL (SQL regeneration), Finalizar Handler (BDD), crearFila (BDD row builder), dibujarDiagrama (ER diagram renderer), dibujarRelaciones (FK arrows), renderTablasTab (editable columns table), validarEstructuraSQL (JS function), Lineamiento BDD Workspace (+8 more)

### Community 9 - "Docker & Dependencies"
Cohesion: 0.25
Nodes (9): TCP-only Healthcheck Rationale, Nginx Service, Web Service (Django/Gunicorn), Python Requirements, Django Dependency, Gunicorn Dependency, Playwright Dependency, psycopg2-binary Dependency (+1 more)

## Knowledge Gaps
- **43 isolated node(s):** `Meta`, `Migration`, `Migration`, `Migration`, `Migration` (+38 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **28 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Usuario` connect `Usuarios & Auth Models` to `Views & Request Handlers`?**
  _High betweenness centrality (0.066) - this node is a cross-community bridge._
- **Why does `FormalizacionFirma` connect `Formalizacion Firmas & Tags` to `Views & Request Handlers`, `Admin & Data Models`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Why does `PostgresBlobStorage` connect `Postgres Blob Storage` to `Admin & Data Models`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Are the 18 inferred relationships involving `LineamientoDetalle` (e.g. with `LineamientoDetalleInline` and `cargar_sql_ajax()`) actually correct?**
  _`LineamientoDetalle` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Formalizacion` (e.g. with `Command` and `_crear_formalizacion_si_no_existe()`) actually correct?**
  _`Formalizacion` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `FormalizacionFirma` (e.g. with `formalizaciones_pendientes_count()` and `firmar_formalizacion_ajax()`) actually correct?**
  _`FormalizacionFirma` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `Usuario` (e.g. with `UsuarioAdminCreationForm` and `UsuarioAdminForm`) actually correct?**
  _`Usuario` has 2 INFERRED edges - model-reasoned connections that need verification._