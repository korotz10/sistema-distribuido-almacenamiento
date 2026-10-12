**Mini Drive Distribuido — versión 2**

Servicio de almacenamiento con un coordinador FastAPI y tres nodos. Cada versión se confirma después de verificar dos copias independientes. El cliente usa nombres lógicos; los nodos guardan objetos con identificadores únicos.

El coordinador sigue siendo una dependencia central. Esta versión protege la consistencia y permite respaldar y recuperar el catálogo; la disponibilidad automática ante la pérdida del equipo coordinador requiere un despliegue adicional.

**Instalación y ejecución**

Entorno validado: Python 3.12. Desde la raíz del repositorio:

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
~~~

Configure dos secretos distintos, de al menos 32 caracteres. Para generar cada uno puede ejecutar:

~~~powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
~~~

- CLIENT_API_KEY: permite usar la API del coordinador.
- NODE_API_KEY: compartida entre el coordinador y los nodos para la comunicación interna.
- Los procesos se niegan a arrancar si falta su clave requerida. No hay claves predeterminadas.
- Las variables se leen del entorno. El archivo .env.example es una plantilla; no se carga automáticamente.

Ejemplo de coordinador:

~~~powershell
$env:CLIENT_API_KEY="REEMPLAZAR_POR_LA_CLAVE_DEL_CLIENTE"
$env:NODE_API_KEY="REEMPLAZAR_POR_LA_CLAVE_INTERNA"
$env:NODE1_URL="http://IP_REAL_NODO_1:8001"
$env:NODE2_URL="http://IP_REAL_NODO_2:8002"
$env:NODE3_URL="http://IP_REAL_NODO_3:8003"
python -m coordinator.main
~~~

Ejemplo de nodo:

~~~powershell
$env:NODE_API_KEY="REEMPLAZAR_POR_LA_MISMA_CLAVE_INTERNA"
$env:NODE_ID="node1"
$env:NODE_PORT="8001"
$env:STORAGE_DIR="./storage/node1"
python -m node.main
~~~

Para node2 y node3, cambie identificador, puerto y directorio. En equipos distintos puede conservar el directorio predeterminado ./storage. Si ya tiene archivos de la versión anterior, mantenga la ruta original de STORAGE_DIR durante la migración. El nodo registra la identidad del directorio y rechaza que otro NODE_ID lo comparta.

Abra /docs en el coordinador y use **Authorize** para introducir CLIENT_API_KEY. En /docs de un nodo use NODE_API_KEY. No introduzca el prefijo Bearer en el cuadro de Swagger; la interfaz lo agrega. Las llamadas HTTP directas deben enviar la cabecera Authorization: Bearer seguida de la clave.

Las direcciones incluidas en common/config.py son valores de configuración, no un inventario de equipos activos. Verifique NODE1_URL, NODE2_URL y NODE3_URL en su red. El proceso escucha por defecto en 0.0.0.0; NODE_HOST y COORDINATOR_HOST permiten restringir la interfaz.

**Contrato público**

| Método y ruta | Comportamiento |
| --- | --- |
| GET /health | Salud del coordinador y acceso al catálogo, sin sondear nodos. No requiere clave. |
| POST /upload | Crea o actualiza un nombre. Devuelve 201 después de verificar dos copias. |
| GET /download/{filename} | Descarga verificada. Si el principal falla o está corrupto, consulta la réplica. |
| GET /files | Metadatos publicados; COMMITTED no es un diagnóstico actual de disponibilidad. |
| GET /status | Sondeo concurrente de identidad y escritura de los nodos. READY describe el nodo. |
| GET /integrity | Lee y comprueba las copias: AVAILABLE, DEGRADED o UNAVAILABLE por archivo. |
| POST /repair/{filename} | Recupera una copia válida y publica una nueva versión con dos copias verificadas. |
| POST /maintenance | Reintenta la limpieza pendiente; también se ejecuta en segundo plano cada 30 s. |

Todas las rutas, salvo /health y las páginas de documentación, requieren CLIENT_API_KEY.

| Código | Significado |
| --- | --- |
| 400 | Nombre inválido o solicitud mal formada. |
| 401 | Credencial ausente o incorrecta. |
| 404 | Nombre no registrado. |
| 408 | La entrada del cliente dejó de enviar datos durante el plazo de lectura. |
| 409 | Otra subida o reparación del mismo nombre está en curso. |
| 413 | Archivo o cuerpo HTTP demasiado grande. |
| 502 | No se lograron dos confirmaciones válidas y hubo metadatos incorrectos. |
| 503 | Catálogo, nodos o copias válidas insuficientes. |
| 504 | Se agotó el plazo de transferencia. |

Un cliente que reciba 409 debe esperar y volver a consultar o reintentar. Si se pierde la respuesta después de un commit, el cliente debe consultar /files o descargar para comprobar el resultado; no hay una garantía de entrega exactamente una vez de la respuesta HTTP.

Los nombres con #, %, espacios, acentos y emojis están soportados. Codifique el segmento del nombre al construir una URL. Se rechazan rutas, controles, nombres reservados de Windows y caracteres incompatibles entre Windows y Unix.

**Publicación de archivos**

1. Una transacción reserva el nombre y asigna un identificador de objeto nuevo. La versión anterior permanece publicada.
2. El coordinador copia la entrada a un temporal y calcula tamaño y SHA-256 por bloques.
3. Sondea los nodos en paralelo, verifica su identidad y selecciona almacenamientos distintos siguiendo round-robin.
4. Envía las primeras dos copias en paralelo. Si una falla, intenta otro nodo disponible.
5. Valida identidad del nodo y almacenamiento, identificador del objeto, tamaño y SHA-256 de cada confirmación.
6. SQLite publica la nueva versión y sus dos ubicaciones en una transacción. Una operación simultánea sobre el mismo nombre recibe 409.
7. Los objetos descartados quedan en una cola persistente de limpieza. Las versiones anteriores se conservan durante un margen que supera el plazo de descarga.

En una descarga, el coordinador verifica una copia completa en disco temporal antes de enviar su contenido. La memoria de transferencia se mantiene acotada; se necesita espacio temporal y la respuesta comienza después de verificar el archivo. El failover incluye contenido corrupto, no solo errores de conexión.

**Persistencia y migración**

El catálogo activo es catalog.sqlite3. Usa transacciones, restricciones de unicidad, WAL y sincronización FULL. No copie únicamente el archivo principal mientras el servicio está activo: utilice el script de respaldo.

La primera inicialización importa catalog.json en una transacción y conserva el JSON original. Las entradas migradas mantienen sus nombres físicos originales; no hace falta mover los archivos. Una vez inicializada la base, el JSON deja de ser la fuente de verdad y no se vuelve a importar.

Si el JSON está dañado o sus metadatos son inválidos, la migración falla y conserva el original. Si SQLite está dañado, las peticiones fallan sin sustituirlo por un catálogo vacío. Corrija o restaure los metadatos antes de volver a operar.

~~~powershell
python scripts/backup_catalog.py ./backups/catalog-2026-10-12.sqlite3
~~~

Guarde los respaldos fuera del equipo coordinador. Para recuperar, detenga los procesos del coordinador y configure CATALOG_DB hacia un respaldo consistente ubicado en una ruta nueva. No sobrescriba una base abierta ni mezcle sus archivos WAL con los de otro respaldo.

Un respaldo antiguo puede referenciar versiones que la limpieza ya eliminó. Para recuperar un punto anterior de forma completa también se necesitan las copias de objetos correspondientes. Un respaldo del catálogo por sí solo no constituye un respaldo histórico de los archivos.

**Configuración adicional**

| Variable | Predeterminado | Uso |
| --- | --- | --- |
| CATALOG_DB | ./catalog.sqlite3 | Ruta del catálogo SQLite; raíz del proyecto por defecto. |
| MAX_FILE_SIZE | 104857600 | Máximo por archivo: 100 MiB. Configure el mismo valor en todos los procesos. |
| MAX_CONCURRENT_TRANSFERS | 4 | Límite de transferencias por proceso. |
| TRANSFER_TIMEOUT | 120 | Plazo en segundos para replicar o recuperar una copia. |
| PENDING_TTL | 600 | Recuperación de reservas abandonadas; mínimo efectivo de dos plazos de transferencia. |
| GARBAGE_GRACE | 300 | Retención de la versión anterior; mínimo efectivo de dos plazos de transferencia. |
| MAINTENANCE_INTERVAL | 30 | Intervalo de limpieza del coordinador, en segundos. |
| COORDINATOR_URL | http://127.0.0.1:8000 | Destino usado por el script de conectividad. |

La reparación tiene una fase de recuperación y otra de replicación, cada una con su plazo. Las reservas incluyen todos los nodos candidatos antes de enviar bytes, de modo que un reinicio o una respuesta perdida no deje copias sin seguimiento.

Un nodo guarda marcas de eliminación en .deleted para bloquear escrituras retrasadas de objetos cancelados. No borre .deleted, .locks ni .node.json durante la operación. Los temporales de procesos muertos se limpian al reiniciar el nodo; los temporales de procesos activos están protegidos por bloqueos.

**Verificación**

~~~powershell
python -m unittest discover -s tests -v
python tests/smoke_http.py
python scripts/test_connectivity.py
~~~

La suite usa directorios temporales y transporte ASGI para reproducir los fallos. La prueba smoke inicia cuatro procesos Uvicorn en puertos locales libres, verifica transferencias de varios MiB, detiene un nodo, reinicia el coordinador y comprueba la reparación. Finaliza sus procesos y elimina sus datos temporales.

El script de conectividad consulta /health del coordinador de forma independiente, usa NODE_API_KEY para sondear los nodos y devuelve código de salida 1 si algún servicio falla. La integridad de los archivos se verifica con /integrity.

La explicación de cada corrección y sus límites está en [docs/CORRECCIONES.md](docs/CORRECCIONES.md).
