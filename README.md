# Asistente de revisión documental

MVP Django para centralizar expedientes de aspirantes, gestionar documentos privados y ordenar la revisión humana. Los datos automáticos son una prevalidación local: no autentican documentos ni consultan organismos oficiales.

## Inicio local con Docker

Necesitas Docker Desktop con Compose.

    Copy-Item .env.example .env
    docker compose up --build -d
    docker compose exec app python manage.py createsuperuser

Abre el puerto indicado en APP_PORT (8000 por defecto). El primer inicio aplica las migraciones. La base PostgreSQL persiste en postgres-data; los documentos y versiones, en private-data. La aplicación y el trabajador montan el mismo almacenamiento privado.

Para cargar expedientes y archivos ficticios de demostración:

    docker compose exec app python manage.py load_demo_data

El comando crea aspirante.demo@example.test y revisor.demo@example.test, con las contraseñas iniciales Demo-Aspirante-2026! y Demo-Revisor-2026!. Conserva las contraseñas en ejecuciones posteriores. Sus archivos contienen texto visible de demostración y no acreditan identidad ni estudios. No uses estas cuentas fuera de desarrollo.

El administrador institucional se crea con `createsuperuser`. Al iniciar sesión en `/ingresar/`, entra al panel de Gestión Documental en `/administracion/`. Las operaciones técnicas de personal y del aviso de privacidad están disponibles en `/admin/`. El registro público solo crea aspirantes. Antes de habilitar el registro institucional, reemplaza el aviso draft-1 por el texto aprobado y publica una nueva versión activa.

Al actualizar una instalación anterior, la migración indica los IDs de los perfiles históricos si encuentra correos o identificaciones equivalentes. Si esto ocurre, verifica esos perfiles en la versión anterior y corrige sus datos antes de volver a migrar.

## Operación

- En `/admisiones/aspirantes/` se consulta la ficha de cada persona. **Nuevo aspirante** abre `/admisiones/aspirantes/nuevo/` y crea un borrador con los requisitos vigentes para el programa y la convocatoria elegidos.
- En `/admisiones/aspirantes/importar/` puedes descargar la plantilla de Excel, completarla y subirla (también se acepta un CSV UTF-8) para revisar la vista previa y confirmar las filas válidas. Cada lote admite 500 filas y 256 KB. Conserva la identificación y el teléfono como texto al preparar el archivo.
- Desde `/admisiones/expedientes/<uuid>/` se cargan, consultan y reemplazan documentos de expedientes creados por admisiones. Los cambios conservan el historial y encolan la misma prevalidación que el portal.
- En la bandeja `/admisiones/` se asignan expedientes enviados. Si un revisor no puede continuar, un administrador usa **Liberar expediente** para dejarlo sin asignar; otro revisor puede tomarlo después. Revisa cada documento y usa **Enviar solicitud de correcciones** para reunir los motivos en una sola notificación. Al reenviar, las nuevas versiones quedan pendientes y las aprobaciones de las versiones que siguen vigentes se conservan.
- El botón **Descargar carpeta (ZIP)** del expediente entrega sus documentos vigentes con nombres estándar (`identificación_requisito_vN`). Cada documento individual se descarga con el mismo nombre.
- Los aspirantes consultan su estado e historial en `/aspirante/`. Para abrir un expediente creado por admisiones, verifica el correo de contacto desde `/vincular/`. Si ya tienes cuenta, inicia sesión antes de solicitar y abrir el enlace en el mismo navegador.
- Desde el menú lateral de `/administracion/`, el personal administrador opera la misma bandeja de admisiones y puede crear, editar o desactivar **Programas**, **Convocatorias** y **Requisitos documentales**. Una convocatoria solo acepta solicitudes entre su fecha de apertura y su fecha de cierre; una fecha vacía no limita ese lado. Los cambios de requisitos se aplican a nuevos expedientes. Cada expediente conserva la configuración con la que se creó.
- El panel /admisiones/indicadores/ calcula sus cifras a partir de expedientes e historial guardados; si no hay muestras, muestra “Sin datos”.
- Las notificaciones se guardan dentro de la aplicación. El trabajador ejecuta OCR y envíos SMTP fuera de la petición web. En desarrollo el backend de correo es console, por lo que los mensajes se ven en los registros de app y worker.
- Los envíos fallidos se reintentan con límite. Un administrador puede reencolarlos desde Django Admin → Envíos de correo → “Reintentar envíos fallidos”.
- Los archivos solo se sirven a su aspirante propietario o a personal revisor con permisos, desde una ruta privada y persistente. No publiques private-data ni la montes en un servidor de archivos estáticos.
- “Documentación aprobada” indica que se revisaron los requisitos documentales; no representa una decisión de admisión académica.

### SMTP

En .env, usa EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend y configura SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_USE_TLS o SMTP_USE_SSL, además de DEFAULT_FROM_EMAIL. Reinicia app y worker después del cambio. El backend local de consola no requiere SMTP.

Configura `APP_PUBLIC_URL` con el origen HTTPS del portal para generar enlaces de vinculación. En desarrollo se admite el origen local en `localhost` o `127.0.0.1` con `DEBUG=1`.

En producción, `app` sirve la aplicación con gunicorn y WhiteNoise entrega los estilos y scripts. Pon delante un proxy HTTPS (por ejemplo, Nginx o Caddy) que reenvíe a `app:8000`. Si el proxy termina el TLS, define `SECURE_SSL_REDIRECT=1` solo cuando Django reciba el encabezado `X-Forwarded-Proto` de ese proxy; de lo contrario, deja el valor en `0`.

Los intentos de inicio de sesión se limitan por cuenta y dirección (`LOGIN_RATE_LIMIT`, 5 por defecto) y por dirección (`LOGIN_IP_RATE_LIMIT`, 20 por defecto). El límite por cuenta solo bloquea una cuenta; el límite por dirección cuenta todos los intentos que llegan desde la misma IP. Detrás de un proxy, esa IP es la del proxy, así que el límite por dirección se comparte entre todos los usuarios. Si eso ocurre en la práctica, sube `LOGIN_IP_RATE_LIMIT` o haz que Django lea la IP real desde un proxy de confianza.

### Pruebas

Dentro del contenedor se prueban permisos, estados, versiones, colas, recuperación de trabajos, reglas, indicadores y rutas; Tesseract está instalado para que también se procese un PDF escaneado sintético.

    docker compose exec app python manage.py test
    docker compose exec app python manage.py check
    docker compose exec app python manage.py makemigrations --check --dry-run

Para probar el recorrido de navegador en Windows, instala dependencias de desarrollo y Chromium:

    .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    .\.venv\Scripts\python.exe -m playwright install chromium
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\browser_tests\run_windows.ps1

El script crea PostgreSQL temporal sin volumen, ejecuta la prueba y elimina ese contenedor al terminar; no usa la base ni los documentos persistentes de la aplicación.

El OCR es local; no se envían archivos o texto extraído a servicios externos. Las reglas por palabras clave y la expresión de identificación son orientativas. Una alerta exige criterio humano; “Sin alertas detectadas”, “No concluyente” y “Error técnico” no prueban autenticidad ni invalidez.

## Copia de seguridad y restauración

Detén temporalmente app y worker para que la copia de base de datos y archivos corresponda al mismo momento. Conserva ambos artefactos cifrados y fuera del host del contenedor. Estos ejemplos usan PowerShell:

    New-Item -ItemType Directory -Force .\backups | Out-Null
    docker compose stop app worker
    docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" -f /tmp/asistente.dump'
    docker compose cp db:/tmp/asistente.dump .\backups\asistente.dump
    docker compose exec -T app tar -czf /tmp/documentos.tar.gz -C /data/private .
    docker compose cp app:/tmp/documentos.tar.gz .\backups\documentos.tar.gz
    docker compose start app worker

Para ensayar una restauración sin tocar los volúmenes activos, usa otro nombre de proyecto. Estos pasos dejan detenido el servicio actual y crean volúmenes nuevos; ajusta APP_PORT si el puerto está ocupado:

    docker compose stop app worker
    $project = "asistente-restauracion"
    $env:APP_PORT = "8013"
    docker compose -p $project up -d db
    docker compose -p $project cp .\backups\asistente.dump db:/tmp/asistente.dump
    docker compose -p $project exec -T db sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/asistente.dump'
    docker compose -p $project up -d app
    docker compose -p $project cp .\backups\documentos.tar.gz app:/tmp/documentos.tar.gz
    docker compose -p $project exec -T app tar -xzf /tmp/documentos.tar.gz -C /data/private
    docker compose -p $project up -d worker

Revisa docker volume ls y el nombre del proyecto antes de restaurar; nunca sobrescribas los volúmenes activos durante un ensayo.

## Configuración institucional antes de uso real

El Compose incluido es para desarrollo: inicia el servidor de desarrollo de Django, contiene credenciales de muestra y no configura TLS, dominio, monitorización ni rotación de secretos. Antes de exponerlo, genera secretos y credenciales nuevos; fija DEBUG=0, ALLOWED_HOSTS, HTTPS y CSRF_TRUSTED_ORIGINS; usa una contraseña PostgreSQL aleatoria con caracteres URL-safe (letras, números, guion o guion bajo); configura proxy TLS, servidor WSGI y entrega de archivos estáticos adecuados al despliegue, SMTP, copias de seguridad cifradas, permisos de volúmenes, alertas operativas, límites de retención y eliminación aprobados; y repite las pruebas en el servidor institucional. La aplicación no se declara lista para producción por compilar o iniciar.

La política de conservación, plazos, eliminación, base institucional del aviso, canales de atención y tratamiento de respaldos deben definirse con la institución antes de cargar expedientes reales. El aviso incluido es un borrador explícito, no una garantía de cumplimiento.

## Documentación

- Diseño y decisiones del MVP: docs/superpowers/specs/2026-11-17-asistente-documental-design.md
- Plan de implementación: docs/superpowers/plans/2026-11-17-asistente-documental.md
- Guía operativa y fórmulas de indicadores: docs/OPERACION.md
### Piloto de pre-revisión con IA local

La pre-revisión es orientativa y se procesa en una cola independiente del OCR y el correo. Analiza cargas nuevas cuando `AI_PREVALIDATION_ENABLED=1` en `.env`. El valor por defecto es `0`. Las versiones anteriores conservan sus resultados y los dictámenes solo aparecen en el expediente interno. El diagnóstico se limita a frases predefinidas para evitar que incluya datos personales.

Antes de habilitar el piloto, verifica el acceso a la GPU de Docker Desktop con WSL 2. Descarga Qwen3‑VL en el volumen del modelo con un contenedor temporal que tiene acceso a internet y que solo monta los pesos.

```powershell
docker run --rm -v appgestiondocumental-ollama-models:/root/.ollama --entrypoint /bin/sh ollama/ollama:0.40.16 -c 'ollama serve >/tmp/ollama.log 2>&1 & for i in 1 2 3 4 5 6 7 8 9 10; do ollama list >/dev/null 2>&1 && break; sleep 1; done; ollama pull qwen3-vl:4b-instruct'
docker compose --profile ai up -d --build
```

Mantén la IA desactivada en el entorno real durante las primeras pruebas. Usa una instancia con base de datos y archivos de prueba para medir legibilidad, clasificación y coincidencia de identidad frente a revisión humana. Registra la versión del modelo, latencia y uso de memoria antes de habilitar cargas reales. En operación Ollama queda en una red interna sin publicar su puerto. Para pausar análisis nuevos, desactiva la variable, recrea la app y detén `worker-ai`. Los trabajos pendientes se conservan para una reactivación.
