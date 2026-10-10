# Mini Drive Distribuido 🚀

Sistema distribuido de almacenamiento y replicación de archivos en red enfocado en alta disponibilidad, transparencia e integridad de datos[cite: 1, 2].

---

## 👥 Integrantes del Equipo

- **Santiago** - Coordinador / Servidor Central (`100.116.61.8:8000`)
- **Ivanna** - Nodo 1 de Almacenamiento (`100.x.x.2:8001`)[cite: 2]
- **Dayana** - Nodo 2 de Almacenamiento (`100.90.207.18:8002`)[cite: 2]
- **Victor** - Nodo 3 de Almacenamiento (`100.x.x.4:8003`)[cite: 2]

---

## 🏗️ Arquitectura y Tecnologías

El sistema sigue un patrón **Coordinador-Trabajadores (Master-Workers)**[cite: 2]:

* **Coordinador (Host A):** Punto de entrada unificado, gestión de catálogo de metadatos, algoritmo de selección de nodos y diagnóstico de salud[cite: 2].
* **Nodos de Almacenamiento (Hosts B, C, D):** Persistencia física de binarios en almacenamiento local (`./storage/`)[cite: 2].
* **Red Overlaid:** Red privada virtual Mesh mediante **Tailscale** (rango `100.64.0.0/10`)[cite: 2].
* **Protocolo:** HTTP/REST con **FastAPI** y **HTTPX** asíncrono sobre Python 3[cite: 2].

---

## ⚙️ Estrategias Implementadas

* **Distribución de Carga:** Asignación cíclica (*Round-Robin*) entre nodos principales[cite: 3].
* **Replicación Síncrona ($k=2$):** Cada archivo se guarda de forma obligatoria en el nodo principal y en una réplica secundaria asignada en anillo[cite: 3].
* **Tolerancia a Fallos:** Conmutación transparente a la réplica ante fallos de lectura/escritura[cite: 3].

---

## 📡 Endpoints de la API

### Coordinador (Puerto `8000`)[cite: 3]
- `POST /upload` - Subida de archivos con replicación síncrona[cite: 3].
- `GET /download/{filename}` - Descarga de archivo (con conmutación automática a la réplica)[cite: 4].
- `GET /files` - Consulta del catálogo de metadatos[cite: 4].
- `GET /status` - Diagnóstico de salud de la red de almacenamiento[cite: 4].

### Nodos de Almacenamiento (Puertos `8001`, `8002`, `8003`)[cite: 4]
- `GET /health` - Estado de disponibilidad del nodo[cite: 4].
- `POST /store` - Guardado de binarios en `./storage/`[cite: 4].
- `GET /retrieve/{filename}` - Lectura y entrega de binarios[cite: 4].
