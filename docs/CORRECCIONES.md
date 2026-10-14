# Correcciones del diagnóstico

> Documento histórico de la implementación ampliada. SQLite, mantenimiento y reparación descritos aquí se retiraron al ajustar el primer laboratorio. Consulte [ALCANCE_LABORATORIO.md](ALCANCE_LABORATORIO.md) y el [README](../README.md) para la versión actual.

Revisión del 12 de octubre de 2026, previa a recibir la guía del laboratorio. La limpieza de esa versión se registra en [LIMPIEZA.md](LIMPIEZA.md).

Se corrigieron los fallos de consistencia y comportamiento reproducidos en el diagnóstico inicial. La implementación mantiene las actualizaciones por nombre y el modelo coordinador–nodos. Las nuevas garantías se apoyan en objetos inmutables, un catálogo SQLite transaccional y verificaciones de integridad.

## Explicación de cada solución

| Error anterior | Solución implementada | Comportamiento resultante |
| --- | --- | --- |
| Una actualización fallida modificaba el archivo confirmado. | Cada subida usa un storage_key nuevo. El nombre público cambia de versión solo en la transacción final, después de verificar dos copias. | Ante un 503 o un timeout anterior al commit, la versión publicada conserva sus bytes, hash y ubicaciones. |
| Dos subidas concurrentes del mismo nombre dejaban copias distintas. | La tabla uploads reserva filename con una restricción UNIQUE compartida por los procesos. Las subidas y reparaciones usan la misma reserva. | Una operación continúa y la otra recibe 409. Los bloqueos no dependen de una variable en memoria de un worker. |
| El JSON truncado se interpretaba como un catálogo vacío. | SQLite sustituye las escrituras directas al JSON. La migración valida e importa los datos dentro de una transacción. Los errores se propagan. | Un catálogo inválido detiene la migración o produce 503; no se vacía ni se sobrescribe silenciosamente. |
| Accesos concurrentes al catálogo perdían actualizaciones. | Cada cambio usa BEGIN IMMEDIATE, restricciones SQL, commit/rollback y conexiones cerradas explícitamente. WAL permite lectores durante la actividad de escritura. | Las actualizaciones de archivos distintos se conservan. Se verificó con 24 operaciones desde conexiones concurrentes. |
| El hash se almacenaba pero no se comprobaba. | El coordinador verifica nombre físico, identidad del nodo y almacenamiento, tamaño y SHA-256 de las confirmaciones. También calcula el tamaño y hash de cada descarga. | Una confirmación inválida no cuenta como réplica. Una descarga corrupta provoca failover; si ninguna copia es válida, devuelve 503. |
| Una réplica fallida dejaba bytes sin registrar. | Antes de contactar nodos se registra la operación y todos sus destinos posibles. Un fallo pasa esos objetos a garbage; el mantenimiento reintenta la eliminación. | Las copias parciales quedan rastreadas hasta limpiarse, incluso si el nodo está temporalmente caído. La limpieza es eventual, no una garantía de eliminación inmediata al responder. |
| Un proceso caído o una respuesta perdida dejaban operaciones sin resolver. | Las reservas vencen tras PENDING_TTL. El mantenimiento recupera reservas abandonadas. La eliminación crea una marca persistente en el nodo. | Una escritura retrasada recibe 410 y no resucita un objeto cancelado. Una confirmación perdida permite usar otro nodo y limpiar posteriormente la copia extra. |
| No se usaba el tercer nodo cuando había fallos. | El coordinador sondea identidades y disponibilidad en paralelo, filtra almacenamientos repetidos y prueba candidatos alternativos. | Una caída permite seguir subiendo mientras existan dos almacenamientos válidos. Un fallo posterior al sondeo también activa el candidato restante. |
| La replicación se describía como simultánea pero era secuencial. | Las dos primeras transferencias se ejecutan en paralelo; los candidatos adicionales se prueban si hace falta. | Se conserva la confirmación síncrona de dos copias y se evita sumar innecesariamente las dos transferencias iniciales. |
| Nombres con # y Unicode fallaban al descargar. | Las URL internas codifican el segmento con quote; FileResponse genera el encabezado de descarga UTF-8. Los objetos nuevos usan identificadores opacos. | Se verificaron nombres con #, %, espacios, acentos y emojis, y también nombres heredados del JSON. |
| Los nombres inválidos aparecían como fallos de infraestructura. | La validación portable se ejecuta antes de reservar o contactar nodos y también en el nodo. | Rutas, controles, nombres reservados y caracteres incompatibles reciben 400. Tamaños excesivos reciben 413. |
| La autenticación añadida impedía arrancar con los comandos habituales. | Se retiraron la validación de claves, las cabeceras Bearer y las dependencias de autenticación de OpenAPI, por decisión del alcance académico. Se mantienen los límites de recursos. | Coordinador y nodos arrancan y operan sin claves; Swagger no exige credenciales. |
| READY/HEALTHY se confundía con archivos íntegros. | /status informa NODE_READINESS; /integrity comprueba realmente tamaño y hash de cada ubicación. | Un proceso activo puede aparecer READY y un archivo UNAVAILABLE. La distinción está en el contrato, sin presentar COMMITTED como disponibilidad actual. |
| No había forma de restaurar redundancia desde la copia sana. | POST /repair/{filename} reserva el nombre, recupera una copia verificada y publica una nueva versión en dos nodos. | Una copia corrupta o ausente puede recuperarse. Si ambas se perdieron, devuelve 503 y conserva los metadatos. |
| El script podía declarar caído al coordinador mientras esperaba sondeos. | GET /health comprueba solamente el coordinador y su catálogo. /status sondea nodos en paralelo. El script consulta los servicios por separado. | La caída de los nodos no provoca un falso diagnóstico de caída del coordinador. El script devuelve un código de salida útil. |
| El coordinador cargaba los archivos completos en RAM. | Se usan temporales y bloques de 1 MiB para preparar subidas y verificar descargas. Se limitan tamaño, cuerpo HTTP, concurrencia y plazos. | El uso de memoria de transferencia queda acotado. Se requiere espacio temporal; la descarga comienza después de verificar el archivo completo. |
| Varios nodos locales podían compartir por accidente el mismo disco lógico. | Cada directorio guarda una identidad de nodo y almacenamiento. Se rechaza abrirlo con otro NODE_ID y se validan las identidades remotas. | Cambiar únicamente el puerto ya no permite fingir independencia sobre el mismo directorio. |
| Los temporales de un proceso muerto quedaban indefinidamente. | Cada temporal del nodo tiene un bloqueo entre procesos. Al reiniciar, el nodo elimina temporales cuyos bloqueos ya están libres. | Se limpian escrituras abandonadas sin borrar temporales de otro worker activo. |
| La documentación prometía garantías inexistentes y mostraba configuración contradictoria. | README.md centraliza arquitectura, instalación, API de coordinador y nodos, configuración, migración, pruebas y limitaciones. Se retiró el README duplicado del nodo. | Las instrucciones corresponden al código actual y distinguen la demostración local de la ejecución en varios equipos. |

## Archivos y responsabilidades

- common/files.py: validación de nombres, lectura por bloques, bloqueos, respuesta temporal y espera segura de operaciones de hilo durante cancelaciones.
- common/limits.py: límites del cuerpo, tiempo de lectura y transferencias concurrentes.
- coordinator/catalog.py: catálogo, importación del JSON, reservas, publicación, cola de limpieza y respaldo.
- coordinator/services.py: comunicación HTTP, validación de identidades y confirmaciones, descarga verificada y limpieza.
- coordinator/main.py: API pública, replicación, failover, auditoría, reparación y tarea periódica de mantenimiento.
- node/router.py: publicación inmutable, escrituras idempotentes, bloqueos y marcas de cancelación.
- scripts/backup_catalog.py: respaldo consistente de SQLite sin copiar una base abierta de forma insegura.
- scripts/test_connectivity.py: diagnóstico independiente de procesos.
- tests/test_system.py y tests/smoke_http.py: regresiones y prueba HTTP con procesos reales.

## Verificación realizada

La suite de regresión completó **42 pruebas**. Incluye sobrescrituras fallidas y exitosas, colisión concurrente, errores y pérdida de confirmaciones, recuperación tras timeout, corrupción con igual tamaño, caída de ambas copias, migración de archivos heredados, catálogo corrupto, reservas expiradas, respaldos, límites de cuerpos con y sin Content-Length, arranque y operaciones sin claves, OpenAPI sin autenticación, nombres Unicode y limpieza de temporales.

Se probó además la cancelación de una petición durante el commit: la transacción pudo terminar y los datos publicados permanecieron. La cancelación durante la replicación dejó los destinos registrados para limpieza.

La prueba HTTP inicia tres nodos y un coordinador Uvicorn sobre 127.0.0.1, en directorios temporales, mediante `python -m node.main` y `python -m coordinator.main`. Verifica una transferencia de más de 3 MiB, SHA-256, nombres Unicode con caracteres de URL, failover tras detener el principal, selección de otros dos nodos, persistencia después de reiniciar el coordinador y reparación. No configura claves ni envía credenciales. En Windows cierra el árbol de cada proceso de prueba para incluir el intérprete que pueda crear el lanzador del entorno virtual.

La versión inicial se verificó con Python 3.12.14 en Windows y las dependencias fijadas en requirements.txt y requirements.lock. La revisión de limpieza se probó también con el entorno virtual existente de Windows y Python 3.14.2. En esta última versión, las dependencias actuales emiten avisos de deprecación y avisos de cierre de temporales multipart al interrumpir cuerpos inválidos; estos avisos no son fallos de las aserciones. No se actualizaron dependencias ni se desplegaron cambios en los otros computadores.

Para repetir:

~~~powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python tests/smoke_http.py
~~~

## Actualización de una instalación existente

1. Detenga la versión anterior y haga una copia de catalog.json y los directorios de almacenamiento.
2. Actualice coordinador y nodos juntos. La versión nueva verifica campos de identidad que los nodos originales no proporcionaban.
3. Configure los identificadores, puertos y URL de los nodos. No se requieren claves de API.
4. Mantenga STORAGE_DIR apuntando a los archivos existentes. No cambie la ubicación de los archivos heredados durante la migración.
5. Inicie los nodos y después el coordinador. La primera inicialización crea catalog.sqlite3 e importa el JSON sin modificarlo.
6. Consulte /status y /integrity. Use /repair/{filename} para archivos degradados que aún tengan una copia válida.
7. Cree un respaldo mediante scripts/backup_catalog.py y consérvelo fuera del equipo coordinador.

El JSON anterior queda como referencia de migración y deja de actualizarse. Las copias huérfanas creadas por la versión antigua no se borran automáticamente: no existe un registro suficiente para atribuirlas con seguridad a operaciones fallidas. La nueva cola rastrea las operaciones de esta versión.

## Límites que permanecen

- El coordinador y su disco siguen siendo un punto único de disponibilidad. Los respaldos permiten recuperación; no proporcionan conmutación automática. Para eliminar ese límite hacen falta un catálogo replicado, coordinación de escritores y acceso del cliente a un coordinador disponible. No deben arrancarse coordinadores independientes con copias divergentes de SQLite.
- La auditoría y reparación son operaciones explícitas. La limpieza de operaciones fallidas sí se reintenta en segundo plano.
- Si desaparecen todas las copias válidas, el sistema detecta la pérdida pero no puede reconstruir los bytes.
- Las versiones antiguas se eliminan después del margen configurado. Restaurar un catálogo histórico exige conservar también los objetos a los que apuntaba; un respaldo de metadatos no sustituye un respaldo completo.
- Las pruebas cubren fallos de procesos y comunicación; no simulan fallos del hardware, del sistema de archivos ni cortes de energía. La durabilidad física final depende del almacenamiento.
