**Diagnóstico general del proyecto — 15 de octubre de 2026**

> Diagnóstico histórico de `a942ed8`. Las correcciones y su validación están en [CORRECCIONES_UI_2026-10-15.md](CORRECCIONES_UI_2026-10-15.md). Posteriormente, el usuario aclaró que había eliminado el HTML accidentalmente; ese incidente ya tiene causa conocida y no fue un fallo de la aplicación.

La versión revisada (`a942ed8`) no puede iniciar el coordinador. La causa principal es la mezcla de contratos de dos versiones distintas del backend durante la incorporación de la interfaz. Además, el panel presenta asignaciones del catálogo como si comprobara copias físicas, y la eliminación de archivos tiene resultados parciales que no comunica correctamente.

El diagnóstico incluye revisión de todos los módulos Python, HTML/JavaScript, configuración, historial, documentación y pruebas; ejecución aislada de regresiones; cuatro procesos Uvicorn comunicándose por HTTP local; y comprobaciones del JavaScript con DOM y HTTP simulados. No se verificaron los cuatro equipos remotos ni Tailscale, ni se hizo una revisión visual en un navegador real.

**Causa y evolución de la regresión**

| Cambio | Qué muestra el historial |
| --- | --- |
| `db66fd4`, antes de los cambios de integración | `services.py` ofrecía funciones síncronas, `NodeError`, validación de identidad, confirmaciones de almacenamiento y verificación de tamaño/SHA-256. |
| `8d7a357`, incorporación de la interfaz | Sustituyó ese módulo por funciones asíncronas que reciben URL y nombres, retirando esas validaciones y la excepción. También recuperó una función de round-robin que llama métodos ya ausentes del catálogo. |
| `96c5f80`, ampliación de la interfaz | Retiró `storage_key` del modelo público `FileInfo`. |
| `3894f92`, integración posterior | Recuperó un coordinador y un nodo compatibles con el diseño síncrono, pero no recuperó `services.py`. |
| `a942ed8`, versión revisada | Añadió eliminación y sondeo con hilos sobre esa combinación todavía incompatible. |

Esto explica la relación temporal con la interfaz. Los cambios no afectaron únicamente a la presentación: reemplazaron también contratos internos y protecciones del almacenamiento.

**Hallazgos priorizados**

**1. Bloqueante: el coordinador falla antes de atender solicitudes.**

`coordinator/main.py:139` registra `services.NodeError`, pero esa clase no existe en `coordinator/services.py`. Importar la aplicación produce:

```text
AttributeError: module 'coordinator.services' has no attribute 'NodeError'
```

Esto impide arrancar con `python -m coordinator.main` o importar `coordinator.main:app`. Afecta API, interfaz y recopilación de pruebas. La suite no llega a ejecutar sus 34 casos; informa un error de importación, no 34 fallos individuales. Evidencias: [arranque](diagnostico-ui/01-original-import.txt) y [suite original](diagnostico-ui/02-original-tests.txt).

**2. Alta: agregar solamente la excepción no resuelve la integración.**

| Uso actual del coordinador | Implementación actual de `services.py` |
| --- | --- |
| `check_node_health(node)` devuelve directamente `(ready, details)`. | Es `async def`, recibe una URL y devuelve una coroutine si no se espera. |
| `store_file_in_node(node, key, content, storage_id)` verifica una copia. | Recibe tres argumentos, no cuatro; no verifica identidad, tamaño ni hash de la confirmación. |
| `retrieve_file_from_node(node, record)` devuelve bytes verificados. | Recibe URL y nombre; no usa el registro ni verifica el contenido. |
| Los errores de comunicación se expresan con `NodeError`. | Se ocultan devolviendo `None` o `(False, None)`. |

Al introducir **solo en memoria y en una copia temporal** la excepción faltante, `/status` y `/upload` fallan con `TypeError: cannot unpack non-iterable coroutine object`. El script `scripts/test_connectivity.py:27` falla por el mismo motivo y no diagnostica realmente los nodos. Véanse [prueba mínima](diagnostico-ui/04-original-only-exception-shim.txt) y [conectividad](diagnostico-ui/03-original-connectivity.txt).

También quedaron `select_nodes_round_robin()` y un catálogo global creado al importar `services.py`. Esa función llama `get_round_robin_index()` y `set_round_robin_index()`, que ya no existen. No es la función usada por la subida actual, pero evidencia código incompatible residual. La creación del catálogo durante la importación produce efectos en disco incluso en herramientas que solo pretenden consultar conectividad.

**3. Alta: el indicador de integridad no comprueba el almacenamiento.**

`coordinator/main.py:290` construye `/nodes/files` exclusivamente desde el catálogo y añade `source: "catalog"`. No consulta el disco de los nodos. `fileHealth()` en `coordinator/static/index.html:196` ignora ese origen y considera la coincidencia del nombre como prueba de una copia real. Tampoco compara SHA-256.

Se creó un catálogo de prueba que asignaba un archivo a dos nodos cuyos directorios estaban vacíos. Ambos respondieron salud correctamente, con `files_stored: 0`. Sin embargo, `/nodes/files` anunció el archivo en ambos y la función JavaScript calculó **“Replicado 2/2”**. La descarga respondió `503`, porque los bytes no existían.

Esto puede ocultar archivos ausentes, corrupción, cambios de directorio y eliminaciones parciales. El README promete inventario físico y detección de archivos no asignados, pero el nodo actual no ofrece `GET /files`: devuelve `404`. Además, antes del primer sondeo `/nodes/files` indica `reachable: false` aunque los nodos estén funcionando; al solicitar `/status` y `/nodes/files` en paralelo puede aparecer una primera vista contradictoria.

Evidencias: [respuestas del backend](diagnostico-ui/07-compatible-transport-targeted.txt) y [evaluación del JavaScript](diagnostico-ui/08-interfaz.json). Estas comprobaciones del backend utilizan el transporte compatible temporal para poder superar el bloqueo de arranque.

**4. Alta: una eliminación fallida puede haber borrado una copia; las confirmaciones no se validan.**

`coordinator/main.py:195` elimina secuencialmente las copias y después modifica el catálogo. Se reprodujo:

1. El principal elimina el objeto.
2. La conexión con la réplica falla.
3. La API responde `503` y “No se eliminó el archivo”.
4. El catálogo conserva `COMMITTED`, aunque solo queda una copia física.
5. `/nodes/files` sigue enumerando las dos asignaciones.

El reintento funciona cuando el nodo vuelve: el borrado del nodo es idempotente. El problema es que el estado parcial y el mensaje no representan lo ocurrido. Por inspección, si falla la escritura del catálogo después de borrar las dos copias, puede conservarse un registro sin ningún objeto físico.

Además, la confirmación se limita a `response.status_code == 200` (`main.py:214`). Dos respuestas `200` con un cuerpo ajeno al contrato bastaron para retirar el archivo del catálogo y devolver éxito **aunque ambas copias permanecieran en disco**. Deben validarse estado, nodo y objeto; y representarse el progreso de una eliminación pendiente. Evidencia: [eliminación parcial, reintento y confirmaciones inválidas](diagnostico-ui/07-compatible-transport-targeted.txt).

**5. Media: el contrato público perdió `storage_key`.**

`common/schemas.py:38` ya no declara ese campo. Aunque el catálogo lo conserva, el `response_model` de `/files` lo filtra. Esto no destruye el mapeo interno ni es por sí mismo un fallo de descarga, pero rompe consumidores que lo necesitan para localizar las copias, incluida `tests/smoke_http.py:119`:

```text
KeyError: 'storage_key'
```

La guía de demostración también pide identificar el objeto físico. `/catalog` no soluciona esa necesidad: devuelve totales y candidatos de la próxima subida, no los registros completos. Evidencia: [integración HTTP con el transporte compatible](diagnostico-ui/06-compatible-transport-http.txt).

**6. Media: el panel puede comunicar una desconexión falsa y retroceder a datos antiguos.**

En `index.html:163`, `refresh()` combina cinco consultas con `Promise.allSettled`. Si falla solo `/status`, muestra “Sin conexión con el coordinador” aunque los otros cuatro endpoints respondan. Un fallo interno de sondeo no equivale a una desconexión.

Además, `setInterval(refresh, 4000)` inicia otra actualización sin esperar la anterior. Se reprodujo una respuesta nueva seguida por otra antigua: el estado terminó con los datos antiguos. No hay control de generación ni cancelación. Tampoco hay un plazo propio para `fetch`; una solicitud pendiente retrasa todo ese refresco. El botón de consulta muestra éxito aunque `refresh()` haya terminado por la rama de error.

La prueba ejecuta el JavaScript real con respuestas y DOM simulados; no demuestra fallos visuales ni comportamiento de descarga en todos los navegadores. Evidencia: [resultado](diagnostico-ui/08-interfaz.json) y [reproductor](diagnostico-ui/comprobar-interfaz.mjs).

**7. Media: el sondeo no cumple plenamente su promesa de tiempo máximo y ausencia de acumulación.**

En `coordinator/main.py:65`, las tareas se envían en paralelo, pero `future.result(timeout=...)` se espera secuencialmente. Con tres tareas de 300 ms y un límite de 40 ms por espera, la petición tardó aproximadamente 125 ms, no 40 ms. Las tareas que exceden el tiempo continúan ejecutándose; el timeout de la espera no cancela la solicitud HTTP.

Por inspección, `probe_lock` serializa los sondeos, pero las peticiones que esperan el bloqueo pueden acumularse. El plazo del sondeo tampoco se transmite al cliente HTTP. Al cerrar la aplicación se cancela la tarea periódica sin esperarla y no se cierra explícitamente `probe_pool`. El impacto bajo carga sostenida no se midió. El experimento está registrado en [comprobaciones específicas](diagnostico-ui/07-compatible-transport-targeted.txt).

**8. Media: las pruebas y la documentación quedaron desalineadas.**

Con el transporte compatible, 33 de las 34 pruebas pasan. La restante, `tests/test_system.py:224`, exige que no existan los nuevos endpoints. Esa expectativa quedó obsoleta al añadir la interfaz. No debe retirarse la verificación de seguridad o contrato completa: debe actualizarse la lista y añadir cobertura de los nuevos comportamientos.

La suite existente no cubre el inventario de la interfaz, los borrados, los refrescos concurrentes ni el ciclo de vida del sondeo. La prueba HTTP sí detecta la ausencia de `storage_key`. No hay configuración de CI versionada que ejecute estas comprobaciones automáticamente.

Otras diferencias documentales verificadas:

- README dice Python 3.9+, pero `str | None` en `node/router.py:78` requiere Python 3.10+ con esta implementación.
- README promete `GET /files` en el nodo, inventario físico y un nivel de integridad que la implementación no ofrece.
- Algunas IP del inventario son marcadores y no coinciden con la configuración; su vigencia remota no se comprobó.
- `.env.example` no se carga automáticamente, como advierte el propio archivo. Editarlo sin exportar variables no cambia las direcciones que usa el proceso.
- `PROBE_INTERVAL` y `PROBE_TIMEOUT` no están incluidos en esa plantilla. La API de eliminación tampoco está en la tabla principal del README.
- Los documentos históricos reflejan versiones anteriores. Sus resultados satisfactorios no certifican este commit.

**Resultados de las comprobaciones**

Se usó Windows con Python 3.14.2, FastAPI 0.110.0, Uvicorn 0.28.0, HTTPX 0.27.0 y python-multipart 0.0.9, coincidentes con las dependencias directas declaradas. No se cambiaron versiones. Persisten avisos de deprecación y recursos de TestClient/AnyIO en los registros.

| Escenario | Resultado |
| --- | --- |
| Código original: importar coordinador | Bloqueado por ausencia de `NodeError`. |
| Código original: suite existente | Error de importación; no se ejecutan los 34 casos funcionales. |
| Código original: script de conectividad | Error de coroutine; no completa el diagnóstico. Se apuntó el coordinador a una dirección local de prueba, no a los equipos reales. |
| Copia temporal: añadir únicamente `NodeError` | `/status` y `/upload` siguen fallando. |
| Copia temporal: recuperar solo `services.py` de `db66fd4` | 33 pruebas pasan; 1 falla por la lista antigua de rutas. |
| Esa misma copia: prueba HTTP | Se completa subida y descarga inicial; se detiene al buscar `storage_key` en `/files`. |
| Copia temporal: transporte compatible + recuperar `storage_key` en `FileInfo` | Prueba HTTP completa satisfactoria: copias físicas, archivo de más de 3 MiB, Unicode, hash, duplicados, failover, nodo alternativo, caída y reinicio del coordinador. |
| Copia temporal: inventario y eliminación | Reproducidos inventario ficticio, eliminación parcial y aceptación de confirmaciones inválidas; reintento idempotente correcto. |
| JavaScript de la interfaz versionada | Reproducidos “Replicado 2/2” sin evidencia física, falsa desconexión y sobrescritura del estado reciente por una respuesta anterior. |

Las variantes temporales son **experimentos para aislar causas**, no correcciones aplicadas al proyecto. El sondeo periódico se desactivó en las pruebas Python (`PROBE_INTERVAL=0`) para evitar consultas remotas e interferencias; `/status` se ejercitó explícitamente. No se certifica el funcionamiento prolongado del sondeo automático.

**Componentes conservados y límites del diseño**

Las pruebas con transporte compatible muestran que se mantienen útiles el catálogo con escritura por sustitución, el rechazo de JSON corrupto, las claves de objeto por intento, el rechazo de nombres duplicados, la identidad del directorio, el límite de tamaño y la migración SQLite→JSON. El núcleo no necesita una reescritura completa para recuperar su comportamiento previo.

Siguen existiendo límites expresos del laboratorio: un coordinador y su catálogo como punto único de fallo; un proceso por catálogo/directorio; archivos pequeños en memoria; subidas serializadas; objetos sobrantes de intentos fallidos; ausencia de reparación automática y de autenticación. Se distinguen de las regresiones introducidas al integrar la interfaz. No se realizó auditoría de vulnerabilidades de dependencias, carga sostenida ni tolerancia a cortes eléctricos.

**Orden recomendado de corrección y criterios de aceptación**

1. Unificar `main.py`, `services.py`, contratos y herramientas sobre una misma interfaz. Para este laboratorio, recuperar y adaptar el transporte síncrono previo es el cambio más acotado. Deben pasar arranque, salud, subidas, failover, validaciones de identidad e integridad y diagnóstico de conectividad.
2. Recuperar el campo `storage_key` o definir explícitamente un contrato sustituto y adaptar todos sus consumidores. La prueba HTTP completa debe pasar sin modificaciones temporales.
3. Separar en el panel **asignación esperada**, **existencia comprobada** e **integridad verificada**. Consultar inventario físico o mostrar “sin verificar”; no anunciar integridad a partir del catálogo. Deben detectarse una copia ausente, ambas ausentes y contenido corrupto.
4. Hacer explícita la eliminación pendiente/parcial y validar las confirmaciones. Probar fallo de cada nodo, reintento y fallo de persistencia del catálogo. No prometer que no se borró nada después de una eliminación parcial.
5. Impedir refrescos solapados o descartar respuestas obsoletas, fijar plazos y distinguir errores de endpoint de desconexiones. Ajustar el sondeo y cerrar sus recursos.
6. Actualizar la suite para las rutas nuevas, añadir regresiones de interfaz/eliminación, ejecutar las pruebas automáticamente y corregir la documentación. Después repetir la demostración en los cuatro equipos reales con la misma revisión de código.

**Incidente del HTML durante el diagnóstico**

`coordinator/static/index.html` estaba presente al comenzar y después apareció eliminado en el directorio de trabajo. El usuario confirmó que no lo había borrado. Se revisaron las operaciones preparadas: las limpiezas de Python se limitan a directorios temporales; no se encontró una orden que eliminara ese archivo ni un enlace de directorio que explicara el incidente. No hay evidencia suficiente para atribuirlo a Git, al editor, a una sincronización o a otra causa.

Se restauró únicamente ese archivo desde `HEAD` y se verificó que su objeto Git coincide exactamente con el versionado: `909ee785759877b5d4359b1a37cb89d51da226e9`. Durante su ausencia, la prueba aislada respondió `404` en la interfaz. Se repitieron las comprobaciones después de recuperarlo. Este incidente no explica el error previo de importación ni las otras incompatibilidades reproducidas.

**Artefactos y reproducción**

El código de aplicación queda sin cambios respecto de `HEAD`; no se modificaron archivos almacenados, catálogos reales ni dependencias. Se añadieron este informe y la carpeta [diagnostico-ui](diagnostico-ui/) con scripts y registros. Los scripts Python usan copias y datos temporales; la integración HTTP abre servidores exclusivamente en `127.0.0.1` y los cierra al terminar.

Desde la raíz del proyecto:

```powershell
.\venv\Scripts\python.exe docs/diagnostico-ui/reproducir.py
node docs/diagnostico-ui/comprobar-interfaz.mjs
```

La segunda comprobación requiere Node.js; en esta revisión se ejecutó mediante el entorno Node disponible en la herramienta. Tras aplicar las correcciones, ambos reproductores se fijaron a la copia histórica de `a942ed8` para conservar la reproducibilidad de este diagnóstico. Los scripts sobrescriben solamente sus propios registros al repetirse. El [manifiesto](diagnostico-ui/resultados.json) conserva los comandos y códigos de salida; un código cero en las comprobaciones específicas significa que el experimento terminó, no que los fallos observados estén corregidos. Para verificar la versión corregida se usan las pruebas de `tests/`.
