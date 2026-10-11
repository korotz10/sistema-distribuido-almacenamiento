```markdown
# Nodo de Almacenamiento

Componente `node/` del **Sistema Distribuido de Almacenamiento (Mini Drive Distribuido)**[cite: 6].

Este README explica la responsabilidad del nodo, su arquitectura interna, cómo configurarlo, cómo ejecutarlo y la guía de pruebas paso a paso[cite: 8].

---

## 1. Qué es y qué hace

Un nodo es un servicio web construido con **FastAPI** que cumple el rol de **trabajador (*worker*)** dentro de la arquitectura Coordinador-Trabajadores[cite: 6, 8, 20]. Su responsabilidad es deliberadamente acotada[cite: 8]:

- **Guardar** archivos binarios en el disco local (`./storage/`) mediante escrituras atómicas cuando el Coordinador se lo solicita[cite: 8, 9, 14].
- **Devolver** la secuencia de bytes de los archivos cuando el Coordinador los requiere[cite: 8, 12, 14].
- **Informar** su estado de salud (`/health`) para que el Coordinador detecte caídas o desconexiones[cite: 10, 14].

Lo que el nodo **NO** hace:

- No decide la ubicación de los archivos (responsabilidad del algoritmo Round-Robin del Coordinador)[cite: 7, 8, 11].
- No gestiona la replicación ni conoce la existencia de los demás nodos[cite: 8, 12, 14].
- No mantiene un catálogo global ni estados persistentes de la red[cite: 8, 9].
- No atiende a los usuarios finales; responde exclusivamente a peticiones del Coordinador[cite: 8, 14].

```text
Cliente ──► Coordinador (:8000) ──► Nodo 1 (Ivanna  - :8001) ──► ./storage/
                                ──► Nodo 2 (Dayana  - :8002) ──► ./storage/
                                ──► Nodo 3 (Victor  - :8003) ──► ./storage/

```

Los tres nodos ejecutan **exactamente el mismo código base**; sus diferencias operativas se gestionan mediante variables de entorno (`NODE_ID`, `NODE_PORT`, `NODE_HOST`).

---

## 2. Estructura del Componente

```text
node/
├── __init__.py   # Inicialización del paquete Python
├── main.py       # Punto de entrada y servidor Uvicorn / FastAPI
└── router.py     # Lógica de endpoints internos (/health, /store, /retrieve)
storage/          # Directorio de almacenamiento físico local (ignorado en Git)

```

---

## 3. Funcionamiento Interno

### A. Guardar un archivo (`POST /store`)

1. El Coordinador transmite el archivo vía `multipart/form-data`.


2. El nodo valida el nombre usando `safe_path()` para prevenir vulnerabilidades de *Path Traversal* (rechaza secuencias como `../` o barras de directorio).


3. Stream del archivo en bloques de **1 MB (`CHUNK_SIZE`)** hacia un archivo temporal `.tmp` en `./storage/`, calculando en tiempo real el tamaño total y la firma **SHA-256**.


4. Ejecuta un vaciado a disco (`os.fsync`) y un renombrado atómico (`os.replace`) hacia el nombre final.


5. Devuelve respuesta `201 Created` con el identificador del nodo, nombre, tamaño y hash.



Si ocurre una falla en el canal durante la transmisión, el archivo `.tmp` se elimina inmediatamente de forma segura.

### B. Devolver un archivo (`GET /retrieve/{filename}`)

1. Valida el nombre del archivo solicitado.


2. Si el archivo existe físicamente en `./storage/`, retorna un `FileResponse` con la secuencia de bytes (`application/octet-stream`).


3. Si el archivo no existe, devuelve `404 Not Found`. El Coordinador interpreta esta respuesta (o un *timeout*) para activar el *failover* y recuperar el recurso desde la réplica.



### C. Monitoreo de Salud (`GET /health`)

Responde un objeto JSON con código `200 OK`, el `node_id` y el número de archivos guardados localmente. Si el proceso o la máquina colapsan, la conexión HTTP falla inmediatamente, permitiendo al Coordinador marcar el nodo como no disponible.

---

## 4. Requisitos e Instalación

* **Python 3.9+**

* **Tailscale** activo y conectado a la red virtual del proyecto (`100.x.x.x`)



Desde la raíz del proyecto, instalar las dependencias requeridas:

```bash
# Creación y activación del entorno virtual
python -m venv venv

# Linux / Mac:
source venv/bin/activate

# Windows (PowerShell):
.\venv\Scripts\activate

# Instalación de dependencias
pip install fastapi uvicorn python-multipart httpx

```

---

## 5. Configuración por Variables de Entorno

| Variable | Valor por Defecto | Descripción |
| --- | --- | --- |
| `NODE_ID` | `node1` | Identificador del nodo (`node1`, `node2`, `node3`)

 |
| `NODE_PORT` | `8001` | Puerto HTTP asignado al servicio

 |
| `NODE_HOST` | `0.0.0.0` | Interfaz de red de escucha (bind a todas las interfaces o IP de Tailscale)

 |
| `STORAGE_DIR` | `./storage` | Ruta absoluta o relativa donde se persisten los archivos

 |

### Asignación oficial del equipo

| Integrante | Rol | `NODE_ID` | IP Tailscale | Puerto |
| --- | --- | --- | --- | --- |
| **Santiago** | Coordinador | N/A | `100.116.61.8` | `8000`<br> |
| **Ivanna** | Nodo 1 | `node1` | `100.x.x.2` | `8001`<br> |
| **Dayana** | Nodo 2 | `node2` | `100.90.207.18` | `8002`<br> |
| **Victor** | Nodo 3 | `node3` | `100.x.x.4` | `8003`<br> |

---

## 6. Ejecución del Servicio

Ejecutar **siempre desde la raíz del proyecto**:

### Linux / macOS

```bash
NODE_ID=node1 NODE_PORT=8001 python -m node.main

```

### Windows (PowerShell)

```powershell
$env:NODE_ID="node1"; $env:NODE_PORT="8001"; python -m node.main

```

### Alternativa directa con Uvicorn

```bash
NODE_ID=node1 uvicorn node.main:app --host 0.0.0.0 --port 8001

```

---

## 7. Contrato de API Interna

### `GET /health`

* **Respuesta (`200 OK`):**

```json
{
  "status": "ok",
  "node_id": "node1",
  "files_stored": 5
}

```

### `POST /store`

* **Estructura de entrada (`multipart/form-data`):**
* `file`: Archivo binario (Obligatorio).


* `filename`: Nombre lógico opcional en texto.




* **Respuesta (`201 Created`):**

```json
{
  "status": "stored",
  "node_id": "node1",
  "filename": "documento.pdf",
  "size_bytes": 1048576,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}

```

### `GET /retrieve/{filename}`

* **Parámetro:** Nombre del archivo en la URL.


* **Respuesta:** Stream de bytes binarios (`200 OK`) o error en JSON (`404 Not Found` / `400 Bad Request`).



### Documentación Interactiva

Con el servidor en ejecución, acceder a `http://localhost:8001/docs` (o sustituir por la IP de Tailscale) para probar las rutas mediante Swagger UI.

---

## 8. Guía de Pruebas y Verificación

### Pruebas Manuales con cURL (Terminal)

```bash
# 1. Crear archivo de prueba
echo "Prueba de almacenamiento distribuido UTP" > test.txt

# 2. Comprobar salud
curl http://localhost:8001/health

# 3. Guardar archivo en disco
curl -F "file=@test.txt" http://localhost:8001/store

# 4. Recuperar contenido
curl http://localhost:8001/retrieve/test.txt

# 5. Probar recuperación de archivo inexistente (Debe retornar 404)
curl -i http://localhost:8001/retrieve/no_existe.txt

```

### Lista de Verificación de Criterios

| Prueba | Resultado Esperado | Criterio de Aceptación |
| --- | --- | --- |
| `GET /health` | HTTP 200 | Retorna JSON con el `node_id` y contador correcto.

 |
| `POST /store` | HTTP 201 | El archivo físico aparece en la carpeta `./storage/`.

 |
| Hash SHA-256 | Coincidencia exacta | El hash devuelto coincide con el hash local del archivo.

 |
| `GET /retrieve` | HTTP 200 | Integridad total de bytes recibidos.

 |
| Path Traversal | HTTP 400 / 404 | Intento de leer `../main.py` es bloqueado por `safe_path()`.

 |
| Escritura Atómica | Sin corrupción | Cortes de energía no dejan archivos temporales incoherentes.

 |

---

## 9. Decisiones de Diseño Técnico

1. **Escritura Atómica (`.tmp` + `os.replace`):** Evita la corrupción parcial de archivos si la conexión de red se interrumpe durante el stream de subida.


2. **Transferencia por Bloques (`CHUNK_SIZE = 1MB`):** Previene picos de consumo de memoria RAM procesando archivos en buffers acotados.


3. **Validación de Seguridad (`safe_path`):** Aísla strictly todas las operaciones de disco dentro del directorio `./storage/`.


4. **Desacoplamiento (*Stateless*):** El nodo no mantiene estado de red ni lógica de coordinación, facilitando su reinicio instantáneo ante fallos.



---

## 10. Archivo `.gitignore` Recomendado

```text
venv/
__pycache__/
*.pyc
storage/*
!storage/.gitkeep
.env

```

```

```