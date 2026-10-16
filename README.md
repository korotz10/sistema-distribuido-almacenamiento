# Mini Drive Distribuido

Mini Drive Distribuido es un servicio de almacenamiento de archivos que distribuye y replica datos entre tres nodos independientes. Está implementado en Python con FastAPI y HTTPX y se comunica mediante HTTP/REST sobre Tailscale. El despliegue del coordinador y los tres nodos se verificó en la red Tailscale del equipo el 15 de octubre de 2026.

---

## 1. Propósito

Este proyecto ofrece una interfaz de almacenamiento unificada: el cliente interactúa con el coordinador, que decide dónde guardar los objetos y registra sus metadatos en un catálogo JSON.

- **Transparencia de ubicación:** el cliente utiliza la API del coordinador sin seleccionar nodos.
- **Replicación síncrona (`k = 2`):** el coordinador sondea los nodos en paralelo y envía las dos primeras copias elegibles concurrentemente; si una falla, prueba el siguiente nodo. Registra la subida después de validar dos confirmaciones de almacenamientos distintos.
- **Recuperación desde réplica:** durante una descarga, si la copia principal no está disponible o no coincide con el tamaño y SHA-256 del catálogo, el coordinador intenta recuperar la réplica.

---

## 2. Arquitectura

La arquitectura separa el plano de control, a cargo del coordinador, del plano de datos, implementado por los nodos:

```text
               +----------------------------------+
               |        CLIENTE / NAVEGADOR       |
               +----------------+-----------------+
                                |
                   (HTTP / REST - Puerto 8000)
                                v
               +----------------------------------+
               |     COORDINADOR (Host A)         |
               |   - Plano de Control             |
               |   - Catálogo de Metadatos        |
               |   - Algoritmo Round-Robin        |
               +----------------+-----------------+
                                |
        +-----------------------+-----------------------+
        | (HTTPX / REST)        | (HTTPX / REST)        | (HTTPX / REST)
        v                       v                       v
+---------------+       +---------------+       +---------------+
| NODO 1 (Host B)|      | NODO 2 (Host C)|      | NODO 3 (Host D)|
| Puerto 8001   |      | Puerto 8002   |      | Puerto 8003   |
| ./storage/    |      | ./storage/    |      | ./storage/    |
+---------------+       +---------------+       +---------------+

```

### Componentes

1. **Coordinador (plano de control, Host A):** expone la API pública, mantiene `catalog.json`, selecciona candidatos mediante rotación Round-Robin, solicita operaciones a los nodos y verifica las copias.
2. **Nodos de almacenamiento (plano de datos, Hosts B, C y D):** servicios independientes que guardan, recuperan, enumeran y eliminan objetos en disco dentro de sus respectivos `STORAGE_DIR`.

El catálogo asocia cada nombre visible con una clave física (`storage_key`), tamaño, SHA-256, nodo principal, réplica e identidad de los almacenamientos. El coordinador verifica que las dos confirmaciones de escritura coincidan con los datos enviados antes de registrar el archivo.

---

## 3. Infraestructura desplegada

| Host | Responsable | Rol en la Arquitectura | IP (Tailscale) | Puerto | Estado |
| --- | --- | --- | --- | --- | --- |
| **A** | Santiago | Coordinador | IP Tailscale del Host A | `8000` | Verificado |
| **B** | Ivanna | Nodo 1 (`node1`) | `100.121.209.117` | `8001` | Verificado |
| **C** | Dayana | Nodo 2 (`node2`) | `100.90.207.18` | `8002` | Verificado |
| **D** | Victor | Nodo 3 (`node3`) | `100.98.240.121` | `8003` | Verificado |

El equipo verificó la comunicación del coordinador con los tres nodos y el funcionamiento de los servicios en Tailscale el 15 de octubre de 2026.

---

## 4. Estructura del proyecto

El código está organizado por componentes:

```text
sistema-distribuido-almacenamiento/
│
├── coordinator/               # API, catálogo, servicios e interfaz del coordinador
│   ├── __init__.py
│   ├── catalog.py             # Persistencia del catálogo JSON
│   ├── main.py                # API pública y lógica del coordinador
│   ├── monitor.py             # Sondeo de salud de nodos
│   ├── services.py            # Comunicación HTTP e integridad de copias
│   └── static/index.html      # Interfaz web servida en /ui/
│
├── node/                      # Aplicación y API de los nodos de almacenamiento
│   ├── __init__.py
│   ├── main.py                # Punto de entrada del proceso del Nodo
│   └── router.py              # Endpoints del nodo e interacción con el disco
│
├── common/                    # Configuración y utilidades compartidas
│   ├── __init__.py
│   ├── config.py              # Direcciones, puertos y tiempos de espera
│   ├── files.py               # Validación de nombres, límites y escritura atómica
│   └── schemas.py             # Modelos de validación Pydantic
│
├── storage/                   # Directorio de almacenamiento local de un nodo
├── scripts/
│   ├── migrate_catalog.py     # Exportación de SQLite heredado a JSON
│   └── test_connectivity.py   # Diagnóstico HTTP de los servicios
├── tests/
│   ├── test_system.py         # Pruebas de API, catálogo y almacenamiento
│   ├── smoke_http.py          # Integración con cuatro procesos locales
│   └── test_ui.mjs            # Pruebas del JavaScript de la interfaz
├── docs/                      # Alcance, demostración y diagnósticos
├── .github/workflows/         # Ejecución automatizada de pruebas
├── .env.example               # Plantilla de variables de entorno
├── requirements.txt           # Dependencias directas de Python
└── README.md

```

---

## 5. Instalación y configuración

### Requisitos

* Python 3.10 o superior.
* Tailscale instalado y conectado en los cuatro equipos.

### Instalar dependencias

```powershell
git clone <URL_DEL_REPOSITORIO>
cd sistema-distribuido-almacenamiento
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

En Linux o macOS:

```bash
git clone <URL_DEL_REPOSITORIO>
cd sistema-distribuido-almacenamiento
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
```

`.env.example` documenta las variables disponibles; la aplicación lee la configuración del entorno del proceso. En PowerShell, se definen antes de iniciar cada servicio. En el coordinador, las direcciones de los nodos se pueden establecer así:

```powershell
$env:NODE1_URL="http://IP_NODO_1:8001"
$env:NODE2_URL="http://IP_NODO_2:8002"
$env:NODE3_URL="http://IP_NODO_3:8003"
```

| Variable | Componente | Valor predeterminado |
| --- | --- | --- |
| `NODE1_URL`, `NODE2_URL`, `NODE3_URL` | Coordinador | Las URL Tailscale definidas en `common/config.py`. |
| `CATALOG_FILE` | Coordinador | `catalog.json` en la raíz del proyecto. |
| `COORDINATOR_HOST`, `COORDINATOR_PORT` | Coordinador | `0.0.0.0`, `8000`. |
| `COORDINATOR_URL` | Script de conectividad | `http://127.0.0.1:8000`. |
| `NODE_ID`, `NODE_PORT`, `NODE_HOST` | Nodo | `node1`, `8001`, `0.0.0.0`; cada equipo asigna el ID y puerto correspondiente. |
| `STORAGE_DIR` | Nodo | `storage` en la raíz del proyecto. |
| `MAX_FILE_SIZE` | Coordinador y nodo | `10485760` bytes (10 MiB). |
| `PROBE_TIMEOUT` | Coordinador | `2` segundos por ronda de salud. |
| `INVENTORY_TIMEOUT` | Coordinador | `5` segundos por consulta de inventario. |

El catálogo se crea al iniciar el coordinador cuando aún no existe. Sus cambios se escriben atómicamente y el JSON se valida al leerlo. Cada nodo crea su directorio de almacenamiento y `.node.json` en su primer arranque. El coordinador reutiliza un cliente HTTP asíncrono durante su vida para las subidas, las comprobaciones de estado y el inventario. Cada petición a `/status` consulta los nodos en paralelo; las peticiones simultáneas comparten la ronda. El inventario físico se consulta mediante una operación independiente.

---

## 6. Ejecución

### Nodos de almacenamiento

Cada integrante inicia el proceso correspondiente en su equipo con el `NODE_ID` y `NODE_PORT` asignados:

En Windows PowerShell, ejecutar una configuración por terminal:

```powershell
$env:NODE_ID="node1"; $env:NODE_PORT="8001"; python -m node.main
```
```powershell
$env:NODE_ID="node2"; $env:NODE_PORT="8002"; python -m node.main
```
```powershell
$env:NODE_ID="node3"; $env:NODE_PORT="8003"; python -m node.main
```

En Linux o macOS, se inicia el nodo asignado con su identificador y puerto:

```bash
NODE_ID=node1 NODE_PORT=8001 python -m node.main
NODE_ID=node2 NODE_PORT=8002 python -m node.main
NODE_ID=node3 NODE_PORT=8003 python -m node.main
```

### Coordinador

En el Host A, iniciar el coordinador en el puerto 8000:

```powershell
python -m coordinator.main

```

---

## 7. Interfaz web

Con el Coordinador en ejecución, abre `http://<IP-del-coordinador>:8000/ui` en el navegador. Muestra:

- **Topología:** estado observado de cada nodo, dirección y tiempos de conexión/respuesta; inventario físico del nodo marcado como **P** (principal), **R** (réplica) o **X** (objeto no asociado al catálogo).
- **Subida:** al subir un archivo se ve a qué nodo fue como principal y dónde quedó la réplica.
- **Catálogo:** archivos con `storage_key`, nodos asignados y estado observado. La interfaz compara clave, tamaño, SHA-256 e identidad del almacenamiento con el inventario físico.
- **Descarga:** informa qué nodo sirvió el archivo y si hubo **failover**; si ninguna copia está disponible, muestra el error de la operación.
- **Actividad:** bitácora de subidas, descargas, failovers y errores.
- **Eliminación:** antes de borrar, el coordinador verifica en paralelo que ambos nodos asignados estén activos y conserven la identidad de almacenamiento esperada. Si no puede verificar cualquiera de ellos, responde `503` sin iniciar el borrado y conserva el archivo como `COMMITTED`. Tras superar esa comprobación registra `DELETING` antes de modificar las copias; si una operación de borrado o la finalización del catálogo falla, el archivo queda pendiente y el botón **Reintentar eliminación** vuelve a solicitar el borrado a ambos nodos, incluso después de reiniciar. Borrar una copia ya ausente es válido. Las descargas y las subidas con ese mismo nombre quedan bloqueadas mientras está pendiente; una falla ocurrida durante la eliminación puede dejar una copia ya borrada y responder `503`.

El panel actualiza al completar cada consulta y espera cinco segundos antes de iniciar otra. Las actualizaciones simultáneas se agrupan. Un error parcial se presenta como consulta incompleta. La bitácora y las fechas de observación de salud se mantienen en memoria del coordinador; las eliminaciones pendientes se guardan en el catálogo.

## 8. Contrato de API

### Coordinador (`http://IP_DEL_COORDINADOR:8000`)

| Método | Ruta | Descripción | Respuesta Esperada |
| --- | --- | --- | --- |
| `POST` | `/upload` | Subida de archivo con Round-Robin y replicación $k=2$ | `201 Created` |
| `GET` | `/download/{filename}` | Descarga con failover; verifica el contenido antes de responder | `200 OK` (bytes del archivo) |
| `GET` | `/files` | Metadatos del catálogo y nodos asignados | `200 OK` (JSON) |
| `GET` | `/status` | Diagnóstico de salud de todos los nodos | `200 OK` (JSON) |
| `GET` | `/health` | Salud del coordinador y lectura de su catálogo | `200` / `503` |
| `GET` | `/nodes/files` | Inventario físico con tamaño y hash; cada nodo puede estar sin verificar | `200 OK` (JSON) |
| `GET` | `/catalog` | Totales y próximos candidatos; la subida puede elegir otros nodos disponibles | `200 OK` (JSON) |
| `GET` | `/events` | Últimos 200 eventos en memoria | `200 OK` (JSON) |
| `DELETE` | `/files/{filename}` | Elimina las copias y luego el registro; admite reintentos pendientes | `200` / `404` / `503` |

`GET /files` informa las ubicaciones registradas en el catálogo. `GET /nodes/files` consulta los objetos que los nodos enumeran desde sus discos. Un nombre no válido devuelve `400`; uno duplicado, `409`; un archivo mayor que `MAX_FILE_SIZE`, `413`. Una descarga de un nombre ausente del catálogo devuelve `404`; si está en estado `DELETING`, devuelve `409`. Si una subida no obtiene dos confirmaciones válidas o no se encuentra una copia íntegra para descargar, se responde `503`.

### Nodos de almacenamiento (`8001`, `8002`, `8003`)

| Método | Ruta | Descripción | Respuesta Esperada |
| --- | --- | --- | --- |
| `GET` | `/health` | Identidad del nodo y número de archivos almacenados | `200 OK` |
| `POST` | `/store` | Guarda el objeto recibido mediante formulario multipart | `201 Created` |
| `GET` | `/retrieve/{filename}` | Devuelve el objeto físico identificado por `filename` | `200 OK` / `404 Not Found` |
| `GET` | `/files` | Enumera objetos físicos con tamaño y SHA-256 | `200 OK` / `503` |
| `DELETE` | `/store/{filename}` | Elimina el objeto y confirma identidad y resultado | `200 OK` (`deleted` o `absent`) |

En las rutas internas, `filename` es el identificador físico del objeto (`storage_key`), que puede diferir del nombre visible. El catálogo conserva compatibilidad con registros heredados que usaban el nombre visible como identificador; los registros nuevos también almacenan la identidad de cada almacenamiento.

---

## 9. Pruebas y verificación

Las pruebas automatizadas y la demostración distribuida verifican aspectos complementarios del sistema.

### Conectividad

El script consulta por HTTP el coordinador y los tres nodos:

```powershell
python scripts/test_connectivity.py

```

### Distribución y replicación

Desde `http://IP_DEL_COORDINADOR:8000/docs`, utiliza `POST /upload` para enviar archivos nuevos y observa en las respuestas los nodos principal y réplica. `GET /files` lista las ubicaciones registradas en el catálogo.

### Inventario físico

`GET /nodes/files` devuelve el inventario informado por cada nodo. Los objetos físicos se identifican mediante `storage_key` y sus hashes pueden compararse con los metadatos del catálogo.

### Recuperación desde la réplica

Al detener el nodo principal de un archivo y solicitar su descarga, el coordinador intenta recuperar el objeto desde la réplica e indica el nodo que atendió la solicitud en la cabecera `X-Storage-Node`.

### Coordinador y persistencia

Los objetos permanecen en los nodos mientras el coordinador está detenido. Al reiniciarlo con el mismo catálogo, se recupera el listado y se puede volver a descargar los archivos.

### Suite automatizada

Desde la raíz del proyecto, con el entorno virtual activo:

```powershell
python -m unittest discover -s tests -v
python tests/smoke_http.py
# Node.js 22 o superior, solo para las pruebas del JavaScript:
node tests/test_ui.mjs
```

Las pruebas Python usan almacenamiento temporal. La prueba HTTP inicia cuatro procesos en puertos libres de `127.0.0.1` y los cierra al finalizar. Las pruebas de interfaz ejecutan el JavaScript con un DOM y respuestas HTTP simulados. GitHub Actions ejecuta estas pruebas en Windows con Python 3.14 y Node.js 22. Node.js se utiliza para las pruebas de interfaz; no es necesario para utilizar el sitio web.

La comunicación entre el coordinador y los tres nodos sobre Tailscale fue verificada por el equipo el 15 de octubre de 2026. El procedimiento y el registro de resultados de la demostración se encuentran en [DEMOSTRACION.md](docs/DEMOSTRACION.md). El alcance académico se describe en [ALCANCE_LABORATORIO.md](docs/ALCANCE_LABORATORIO.md); el [diagnóstico](docs/DIAGNOSTICO_2026-10-15.md) y el [registro de correcciones](docs/CORRECCIONES_UI_2026-10-15.md) documentan las revisiones del 15 de octubre.

## 10. Catálogo y migración

El coordinador persiste en `catalog.json` los nombres visibles, los identificadores físicos, tamaño, SHA-256, nodos asignados, estado, identidades de almacenamiento y fecha de creación.

`scripts/migrate_catalog.py` exporta a JSON catálogos SQLite de una versión anterior. El comando recibe la ruta de origen SQLite y una ruta de destino JSON:

```powershell
python scripts/migrate_catalog.py .\catalog.sqlite3 .\catalog.json
```
