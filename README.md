Aquí tienes un **`README.md` completo, profesional y perfectamente estructurado** para la raíz del proyecto. Está diseñado siguiendo los estándares de software distribuido y listo para ser presentado al profesor y evaluadores.

---

```markdown
# 📁 Mini Drive Distribuido

Un sistema distribuido de almacenamiento y replicación de archivos en red basado en el patrón **Coordinador-Trabajadores (Master-Worker)**, desarrollado con **Python 3**, **FastAPI**, **HTTPX** y desplegado sobre una red privada virtual Mesh con **Tailscale**.

---

## 🚀 1. Propósito del Proyecto

El almacenamiento centralizado en un único servidor genera un punto único de falla (*Single Point of Failure - SPOF*). Este proyecto resuelve dicho problema ofreciendo un servicio de almacenamiento unificado que abstrae la ubicación física de los archivos mediante:

- **Transparencia de Ubicación:** El cliente interactúa únicamente con el Coordinador; no necesita conocer qué nodos existen ni dónde se almacenan físicamente los bytes.
- **Replicación Síncrona ($k=2$):** Cada archivo se guarda simultáneamente en dos nodos físicos independientes antes de confirmar la subida.
- **Tolerancia a Fallos y Failover Transparente:** Si el nodo principal cae o pierde conectividad, el sistema redirige la descarga a la réplica sin interrumpir al usuario ni retornar error.

---

## 🛠️ 2. Arquitectura del Sistema

El sistema adopta una arquitectura desacoplada **Master-Worker** dividida en dos planos de operación:

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

### Componentes:

1. **Coordinador (Plano de Control - Host A):** Punto de entrada unificado. Mantiene el catálogo en `catalog.json`, aplica la rotación Round-Robin, gestiona la replicación síncrona y coordina el failover.
2. **Nodos de Almacenamiento (Plano de Datos - Hosts B, C, D):** Servicios independientes y sin estado (*stateless*) encargados exclusivamente de guardar y leer los bytes físicos en sus directorios locales (`./storage/`).

---

## 👥 3. Inventario de Infraestructura

| Host | Responsable | Rol en la Arquitectura | IP (Tailscale) | Puerto | Estado |
| --- | --- | --- | --- | --- | --- |
| **A** | Santiago | Coordinador / Servidor Central | `100.116.61.8` | `8000` | Activo |
| **B** | Ivanna | Nodo 1 (Almacenamiento) | `100.x.x.2` | `8001` | Activo |
| **C** | Dayana | Nodo 2 (Almacenamiento) | `100.90.207.18` | `8002` | Activo |
| **D** | Victor | Nodo 3 (Almacenamiento) | `100.x.x.4` | `8003` | Activo |

---

## 📂 4. Estructura del Código Fuente

El código está estrictamente organizado por componentes:

```text
sistema-distribuido-almacenamiento/
│
├── coordinator/               # 🧠 COMPONENTE COORDINADOR (Host A)
│   ├── __init__.py
│   ├── catalog.py             # Persistencia y gestión del catálogo catalog.json
│   ├── main.py                # Endpoints públicos FastAPI (Puerto 8000)
│   └── services.py            # Round-Robin, Replicación Síncrona y Failover
│
├── node/                      # 💾 COMPONENTE NODOS TRABAJADORES (Hosts B, C, D)
│   ├── __init__.py
│   ├── main.py                # Punto de entrada del proceso del Nodo
│   └── router.py              # Endpoints internos (/health, /store, /retrieve)
│
├── common/                    # 🛠️ MÓDULO DE CONFIGURACIÓN COMPARTIDO
│   ├── __init__.py
│   ├── config.py              # IPs de Tailscale, puertos y timeouts
│   └── schemas.py             # Modelos de validación Pydantic
│
├── storage/                   # 📁 ALMACENAMIENTO FÍSICO DE ARCHIVOS
│   └── .gitkeep               # Directorio local de persistencia
│
├── scripts/                   # 🧪 SCRIPTS AUXILIARES DE PRUEBA
│   └── test_connectivity.py   # Diagnóstico rápido de red
│
├── catalog.json               # Catálogo central de metadatos (Coordinador)
├── requirements.txt           # Dependencias de Python
├── .gitignore
└── README.md                  # Documentación principal del sistema

```

---

## ⚙️ 5. Instalación y Configuración

### Requisitos Previos

* Python 3.9 o superior.
* Tailscale instalado y activo en los 4 computadores.

### 1. Clonar el repositorio e instalar dependencias

```bash
# Clonar repositorio
git clone <URL_DEL_REPOSITORIO>
cd sistema-distribuido-almacenamiento

# Crear entorno virtual
python -m venv venv

# Activar entorno virtual
# En Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# En Linux / macOS:
source venv/bin/activate

# Instalar librerías requeridas
pip install -r requirements.txt

```

---

## 🚀 6. Guía de Ejecución

### Opción A: Ejecutar un Nodo de Almacenamiento (Ivanna, Dayana, Victor)

Cada integrante ejecuta en su computador el servicio del nodo indicando su `NODE_ID` y `NODE_PORT`:

**En Windows (PowerShell):**

```powershell
# Nodo 1 (Ivanna)
$env:NODE_ID="node1"; $env:NODE_PORT="8001"; python -m node.main

# Nodo 2 (Dayana)
$env:NODE_ID="node2"; $env:NODE_PORT="8002"; python -m node.main

# Nodo 3 (Victor)
$env:NODE_ID="node3"; $env:NODE_PORT="8003"; python -m node.main

```

**En Linux / macOS:**

```bash
NODE_ID=node1 NODE_PORT=8001 python -m node.main

```

### Opción B: Ejecutar el Coordinador (Santiago)

En el Host A, se inicia el servidor maestro en el puerto 8000:

```powershell
python -m coordinator.main

```

---

## 📡 7. Contrato de API (Endpoints)

### Endpoints Públicos del Coordinador (`http://100.116.61.8:8000`)

| Método | Ruta | Descripción | Respuesta Esperada |
| --- | --- | --- | --- |
| `POST` | `/upload` | Subida de archivo con Round-Robin y replicación $k=2$ | `201 Created` |
| `GET` | `/download/{filename}` | Descarga con failover transparente ante caídas | `200 OK` (Stream de bytes) |
| `GET` | `/files` | Lista del catálogo completo y ubicaciones físicas | `200 OK` (JSON) |
| `GET` | `/status` | Diagnóstico de salud de todos los nodos | `200 OK` (JSON) |

### Endpoints Internos de los Nodos (Puertos `8001`, `8002`, `8003`)

| Método | Ruta | Descripción | Respuesta Esperada |
| --- | --- | --- | --- |
| `GET` | `/health` | Estado de salud y cantidad de archivos almacenados | `200 OK` |
| `POST` | `/store` | Guarda bytes en disco local (`./storage/`) atómicamente | `201 Created` |
| `GET` | `/retrieve/{filename}` | Retorna bytes del archivo desde el disco local | `200 OK` / `404 Not Found` |

---

## 🧪 8. Plan de Pruebas y Verificación (Bitácora)

Para verificar el correcto funcionamiento del sistema distribuido:

### 1. Prueba P01 - Conectividad de Red

Ejecutar el script de diagnóstico para validar la red Mesh de Tailscale:

```powershell
python scripts/test_connectivity.py

```

### 2. Prueba P02 - Distribución y Replicación

Abre en el navegador `http://localhost:8000/docs` e interactúa con el endpoint `POST /upload`. Sube tres archivos consecutivos y verifica que el catálogo `catalog.json` distribuya los archivos rotativamente entre los nodos.

### 3. Prueba P03 - Verificación Física

Revisa los directorios `./storage/` en los equipos de los nodos para corroborar que los archivos binarios existan físicamente.

### 4. Prueba P04 - Tolerancia a Fallos (Failover)

1. Detén el servicio en el **Nodo 1** (presionando `Ctrl + C` en su terminal).
2. Solicita la descarga mediante `GET /download/{filename}` de un archivo cuyo nodo principal era el **Nodo 1**.
3. Observa cómo el Coordinador redirige la petición hacia la réplica en el **Nodo 2** y entrega el archivo exitosamente.

### 5. Prueba P05 - Análisis de Punto Único de Fallo (SPOF)

Detén el proceso del Coordinador manteniendo activos los nodos. Demuestra que la interfaz pública deja de responder (Single Point of Failure), aunque la información permanezca intacta en las carpetas `./storage/` de los nodos.