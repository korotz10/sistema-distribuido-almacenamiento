**Diagnóstico de Mini Drive Distribuido — 12 de octubre de 2026**

> Documento histórico del diagnóstico inicial. Describe la versión anterior a las correcciones y a la simplificación académica. El alcance vigente está en [ALCANCE_LABORATORIO.md](ALCANCE_LABORATORIO.md) y en el [README](../README.md).

El proyecto implementa un prototipo funcional de almacenamiento con dos copias y recuperación desde una réplica. La distribución y el failover básico funcionan, pero el sistema no garantiza la consistencia de los archivos confirmados cuando hay sobrescrituras, concurrencia o fallos parciales. Su comportamiento actual resulta adecuado para demostrar conceptos de sistemas distribuidos en un entorno controlado; requiere corregir los problemas de integridad y metadatos antes de confiarle información importante.

**Alcance y método**

Se revisaron todos los módulos Python, la configuración, los contratos de API, el catálogo y la documentación. Se ejecutaron 17 comprobaciones sobre una copia temporal del proyecto, usando las cuatro dependencias directas fijadas en `requirements.txt` y Python 3.12.14. Las aplicaciones FastAPI se ejercitaron mediante transporte ASGI en memoria; cada nodo tuvo un directorio físico independiente. Se simularon conexiones fallidas, respuestas incorrectas y un orden controlado de operaciones concurrentes.

Estas pruebas comprueban la lógica de los endpoints y las escrituras reales en disco. No verifican el despliegue remoto, la conectividad Tailscale, Uvicorn sobre sockets, los tiempos reales de red ni la resistencia ante un corte eléctrico. El código y los datos originales del repositorio no se modificaron. El catálogo original estaba vacío.

**Funcionamiento observado**

1. El cliente envía un archivo a `POST /upload`. El coordinador lee todo su contenido en memoria.
2. El coordinador consulta el índice persistido del round-robin y elige los pares `(node1, node2)`, `(node2, node3)` y `(node3, node1)`. Avanza el índice antes de intentar almacenar el archivo, incluso si la operación falla.
3. Envía el archivo al principal y espera su respuesta. Después lo envía a la réplica y vuelve a esperar. La replicación es síncrona porque se espera a ambos nodos antes de confirmar; las transferencias son secuenciales.
4. Cada nodo valida el nombre, escribe en bloques de 1 MiB a un temporal, calcula SHA-256, ejecuta `fsync` y sustituye el archivo final mediante `os.replace`. Esta atomicidad corresponde a una escritura local; no constituye una transacción entre nodos y catálogo.
5. Cuando ambas respuestas resultan satisfactorias, el coordinador registra los metadatos en `catalog.json` y devuelve `201` con estado `COMMITTED`.
6. Para descargar, busca el nombre en el catálogo y solicita el contenido al principal. Si no recibe una respuesta `200`, intenta la réplica. Si tampoco puede recuperarlo, responde `503`. Un nombre ausente del catálogo produce `404`, aunque sus bytes existan en algún nodo.
7. `/files` muestra metadatos persistidos; `/status` sondea los nodos cuando se invoca. No existe un proceso de reparación o reconciliación en segundo plano.

Referencias: `coordinator/main.py:45`, `coordinator/services.py:19`, `node/router.py:64`, `coordinator/main.py:103`.

**Hallazgos priorizados**

| Prioridad | Hallazgo | Consecuencia y evidencia |
| --- | --- | --- |
| Crítica | Una sobrescritura fallida altera un archivo confirmado. | Después de guardar una versión anterior, se repitió el nombre sobre el mismo par de nodos y se hizo fallar la réplica. La subida devolvió `503`, pero el principal entregó la versión nueva y la réplica la anterior; el catálogo conservó el hash anterior. |
| Crítica | Dos subidas concurrentes con el mismo nombre dejan réplicas diferentes. | Ambas devolvieron `201`. El catálogo señaló `node2` y `node3`, pero sus contenidos fueron `VERSION_A` y `VERSION_B`. Se reprodujo con un orden controlado de solicitudes, sin necesitar varios procesos del coordinador. |
| Alta | El catálogo puede perder metadatos silenciosamente. | Ante JSON truncado, `_read()` devolvió un catálogo vacío. La siguiente escritura sustituyó los registros anteriores. La escritura directa con modo `w` carece de sustitución atómica, recuperación y bloqueo. |
| Alta | No se verifica la integridad antes de confirmar ni entregar. | Respuestas de los nodos con nombre, tamaño y hash incorrectos produjeron `COMMITTED`. Al alterar físicamente el principal, la descarga devolvió los bytes corruptos con `200`, sin consultar la réplica sana. |
| Alta | Una réplica fallida deja archivos sin registrar. | La subida devolvió `503`; el archivo quedó en el principal, pero no en el catálogo. La descarga pública respondió `404`. No hay compensación, registro de operación pendiente ni limpieza posterior. |
| Alta | El coordinador y su catálogo son puntos únicos de fallo. | Todas las operaciones públicas dependen de un proceso y un archivo local. La replicación de los bytes no mantiene disponible la API si cae el coordinador ni reconstruye automáticamente los metadatos perdidos. Hallazgo de arquitectura. |
| Alta | Los endpoints carecen de autenticación y autorización. | Una solicitud directa al nodo pudo guardar y leer un archivo sin credenciales. La aplicación no limita el acceso al coordinador. La exposición efectiva depende de las interfaces, el cortafuegos y las reglas de Tailscale del despliegue. |
| Media | Las subidas no sustituyen nodos caídos. | Con el principal inaccesible y otros dos nodos disponibles, `/upload` devolvió `503` sin intentar un par alternativo. Si cae un nodo de los tres, solo uno de los tres pares del ciclo puede completar nuevas subidas. |
| Media | Hay nombres aceptados que no pueden descargarse. | `a#b.txt` se subió con `201`, pero descargó con `503`: la URL interna interpretó `#b.txt` como fragmento. Un nombre con emoji se subió con `201`, pero descargó con `500` por la construcción del encabezado `Content-Disposition`. |
| Media | Se ocultan errores de validación y de infraestructura. | El nodo rechazó `.env` con `400`, pero el coordinador lo presentó como `503`. Los métodos de comunicación agrupan estados HTTP y excepciones diferentes como ausencia de resultado. |
| Media | Los indicadores de salud no comprueban los datos. | Tras borrar las copias de un archivo de prueba, `/status` mostró los tres nodos `HEALTHY` y `/files` conservó `COMMITTED`; la descarga respondió `503`. |
| Media | El coordinador limita el tamaño y la concurrencia prácticos. | Las subidas usan `await file.read()` sin límite; las descargas cargan `response.content` completo. El consumo de memoria crece con el tamaño y la cantidad de transferencias concurrentes. Las escrituras de disco y catálogo son síncronas dentro de rutas asíncronas. Hallazgo de código, sin prueba de carga. |

Las causas de los fallos de sobrescritura y concurrencia están en el almacenamiento por nombre mutable (`node/router.py:94`) y en la ausencia de coordinación de versiones en `coordinator/main.py:63–88`. El indicador `COMMITTED` expresa que hubo dos respuestas satisfactorias, pero no asegura que las dos ubicaciones sigan conteniendo la misma versión.

La pérdida concurrente de actualizaciones del catálogo también se reprodujo con dos gestores: ambos leyeron el mismo estado y escribieron actualizaciones distintas; quedó un registro de los dos esperados. Esta comprobación fuerza acceso concurrente mediante hilos y representa un riesgo al usar varios procesos o accesos simultáneos al catálogo. No implica que cada operación síncrona del catálogo se intercale por sí sola en el bucle de un único worker.

Referencias adicionales: `coordinator/catalog.py:25–36`, `coordinator/catalog.py:49`, `coordinator/services.py:49`, `coordinator/services.py:67`, `coordinator/main.py:120`, `coordinator/main.py:150`.

**Resultados de las 17 comprobaciones**

| N.º | Escenario | Resultado observado |
| --- | --- | --- |
| 1 | Tres subidas consecutivas | Rotación correcta y dos archivos físicos por subida. |
| 2 | Principal inaccesible durante una descarga | Réplica consultada y contenido correcto, `200`. |
| 3 | Ambas copias inaccesibles | `503`. |
| 4 | Principal inaccesible, otros dos nodos disponibles | `503`, sin sustitución del principal. |
| 5 | Falla la réplica de una subida nueva | `503`, archivo huérfano en el principal y descarga `404`. |
| 6 | Falla la réplica al sobrescribir | Principal nuevo, réplica anterior, hash del catálogo anterior. |
| 7 | Dos subidas concurrentes del mismo nombre | Dos respuestas `201` con copias finales distintas. |
| 8 | Principal alterado después de la subida | Contenido corrupto servido con `200`, sin failover. |
| 9 | Confirmaciones con metadatos incorrectos | Subida aceptada con `201` y `COMMITTED`. |
| 10 | Nombre reservado y recorrido de directorios | `.env`: nodo `400`, coordinador `503`; `../outside.txt`: nodo `400`. |
| 11 | Acceso directo sin credenciales al nodo | Escritura `201` y lectura `200`. |
| 12 | Nombre `a#b.txt` | Subida `201`, descarga `503`. |
| 13 | Nombre `a?b.txt` en Windows | Subida `503`, descarga `404`; Windows rechaza ese nombre. |
| 14 | Nombre con emoji | Subida `201`, descarga `500`. |
| 15 | Nodos activos con todas las copias borradas | Tres `HEALTHY`, metadatos `COMMITTED`, descarga `503`. |
| 16 | Catálogo truncado y escritura posterior | Lectura vacía y desaparición de los registros anteriores. |
| 17 | Dos actualizaciones concurrentes del catálogo | Un registro persistido de dos esperados. |

Todos los módulos Python superaron la comprobación de sintaxis. El script existente `scripts/test_connectivity.py` sondea servicios, pero no verifica replicación, integridad, sobrescrituras ni concurrencia. El plan del README depende principalmente de pruebas manuales.

**Observaciones operativas y documentales**

- El round-robin reparte turnos, no carga real: no considera tamaño de archivos, espacio libre o velocidad de los nodos.
- `/status` realiza las consultas en secuencia. Con tres nodos que agotan su espera, la latencia puede acercarse a nueve segundos en un escenario habitual de timeout. El script de conectividad concede solo tres segundos a esa ruta, por lo que puede declarar el coordinador no disponible cuando en realidad está esperando a los nodos. Es una inferencia del código; no se midió en la red real.
- Los timeouts de HTTPX corresponden a fases de conexión y E/S; no deben interpretarse como un plazo total garantizado para toda la transferencia.
- La configuración contiene direcciones concretas que no coinciden completamente con el inventario del README. Deben verificarse en el despliegue. Además, `/health` no valida la identidad esperada del nodo, y dos URL mal configuradas podrían apuntar al mismo almacenamiento sin que el coordinador lo advierta.
- Si se ejecutan varios nodos en una misma copia local, hay que asignar un `STORAGE_DIR` distinto a cada uno. El directorio por defecto es común; cambiar solamente el puerto y el identificador no crea discos independientes.
- El README describe almacenamiento simultáneo y respuestas en streaming, pero el coordinador transfiere las copias en secuencia y carga las descargas completas en memoria. Los nodos sí usan lectura por bloques al escribir y `FileResponse` al devolver archivos.
- Una caída abrupta puede dejar temporales ocultos: la limpieza está en el manejo de excepciones, que no se ejecuta cuando el proceso termina sin posibilidad de recuperarse. La atomicidad local evita publicar una escritura incompleta, pero no garantiza una transacción distribuida ni limpieza tras cualquier interrupción.
- Las dependencias directas están fijadas; las transitivas no tienen un archivo de bloqueo. El comportamiento validado corresponde al entorno temporal instalado en esta revisión.

**Orden recomendado de corrección**

1. Definir la política de nombres repetidos. Como medida inicial, rechazar duplicados con `409` y coordinar solicitudes simultáneas. Para admitir actualizaciones, almacenar versiones inmutables con identificadores únicos y publicar la nueva referencia solo después de confirmar ambas copias. Un bloqueo por sí solo no resuelve una actualización parcial de dos nodos.
2. Hacer transaccional la persistencia de metadatos. No convertir errores de lectura en un catálogo vacío. Incorporar recuperación, respaldos y control de concurrencia. Conservar el JSON exigiría al menos escritura temporal y sustitución atómica, además del bloqueo correspondiente.
3. Validar las confirmaciones contra nombre, tamaño, hash e identidad esperados. Comprobar el contenido descargado y consultar la réplica cuando la integridad no coincida.
4. Registrar operaciones pendientes y resolver sus fallos parciales. Seleccionar nodos disponibles y añadir reconciliación de copias huérfanas, ausentes o divergentes.
5. Aplicar autenticación entre cliente, coordinador y nodos; restringir el acceso de red según el despliegue. Codificar los nombres en URL y encabezados, y preservar la distinción entre errores del cliente y fallos del servicio.
6. Limitar tamaños y concurrencia, transferir contenido sin cargarlo completo en memoria y separar salud del proceso de integridad de los archivos. Añadir pruebas de regresión para los fallos reproducidos.

Evidencia de ejecución: `C:/Users/victo/AppData/Local/Temp/mini-drive-diagnostic-20261012/run-6wcpghnr/results.json`. El guion de comprobación quedó en `C:/Users/victo/AppData/Local/Temp/mini-drive-diagnostic-20261012/diagnose.py`.
