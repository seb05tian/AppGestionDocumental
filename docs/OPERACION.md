# Guía breve de operación

## Convenciones de revisión

El estado del expediente y el del documento son independientes. Un documento reemplazado conserva su versión y decisiones; la nueva versión queda pendiente de revisión. Cuando un expediente aprobado se reabre, inicia una nueva ronda humana y los documentos vigentes vuelven a aparecer pendientes en esa ronda. El historial y las revisiones anteriores permanecen consultables.

Los resultados automáticos están limitados a reglas explícitas sobre texto local: posible tipo, número de identificación, coincidencia con el registro y mismo hash en requisitos distintos. La ausencia de alerta no acredita autenticidad. Los estados no concluyentes y de error permiten continuar la decisión manual sin invalidar el archivo.

Las palabras clave se configuran con OCR_IDENTITY_KEYWORDS, OCR_SABER11_KEYWORDS, OCR_GRADUATION_KEYWORDS y OCR_DIPLOMA_KEYWORDS en .env, separadas por |. La expresión OCR_IDENTIFICATION_PATTERN controla el patrón de identificación. Al cambiar una regla, cambia OCR_RULES_VERSION y reinicia worker; cada resultado conserva la versión aplicada.

## Fórmulas del panel

Todos los indicadores usan los registros existentes y la zona horaria institucional configurada (America/Bogota):

- Expedientes por estado: conteo actual de solicitudes por estado.
- Pendientes de revisión: solicitudes en Enviado o En revisión; excluye borradores, correcciones a cargo del aspirante, aprobados y cerrados.
- Tiempo hasta primera revisión: promedio entre submitted_at y la primera transición de Enviado a En revisión; incluye solo expedientes con ambas fechas.
- Tiempo hasta aprobación documental: promedio entre submitted_at y la primera transición a Documentación aprobada; incluye solo expedientes que llegaron a esa transición.
- Expedientes con correcciones: solicitudes distintas que tuvieron al menos una transición a Pendiente de corrección, divididas por solicitudes que fueron enviadas; se presenta como porcentaje.
- Documentos no concluyentes o con error técnico: conteo de resultados con esos estados para versiones vigentes de requisitos obligatorios. No cuenta alertas que requieren revisión humana.

Los tiempos son promedios, no metas ni promesas. Los casos sin fechas suficientes se omiten y se muestra “Sin datos” cuando no hay muestra.

## Privacidad y conservación

La migración inicial incluye un aviso marcado BORRADOR institucional. Un administrador debe cargar y activar la versión que la institución apruebe; cada registro conserva la versión aceptada. Este MVP no elimina automáticamente expedientes, archivos, texto OCR, historial ni respaldos. La institución debe definir plazos y responsables para conservación, eliminación, solicitudes de titulares y copias de respaldo antes del piloto con datos personales.

## Recuperación operativa

run_worker reclama trabajos con bloqueo de base de datos, reintenta con límite y recupera filas estancadas según minutos configurados. La versión del trabajo determina el resultado que actualiza; nunca cambia el puntero de versión vigente. El resultado automático no crea decisiones humanas. Las fallas de correo quedan en EmailOutbox y se pueden reintentar desde Admin.

SMTP es un sistema externo y no soporta una garantía transaccional de entrega exactamente una vez: si el servidor acepta un correo justo antes de que el trabajador se interrumpa, una recuperación podría enviarlo de nuevo. La notificación dentro de la aplicación y la decisión guardada no se duplican por ello.
