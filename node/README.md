# Nodo de Almacenamiento

Componente `node/` del **Sistema Distribuido de Almacenamiento (Mini Drive Distribuido)**.

Este README explica qué es un nodo, cómo funciona internamente, cómo ejecutarlo y cómo probarlo.

---

## 1. Qué es y qué hace

Un nodo es un servicio web (FastAPI) que cumple el rol de **trabajador** dentro de la
arquitectura Coordinador-Trabajadores. Su responsabilidad es mínima y deliberada:

- **Guardar** archivos binarios en el disco local (`./storage/`) cuando el Coordinador se lo pide.
- **Devolver** esos archivos cuando el Coordinador los solicita.
- **Informar** si está vivo, para que el Coordinador pueda detectar caídas.

Lo que el nodo **no** hace:

- No decide a qué nodo va cada archivo (eso es el Round-Robin del Coordinador).
- No replica ni conoce a los demás nodos (la replicación la coordina el Coordinador).
- No mantiene catálogo ni metadatos.
- No atiende a los usuarios finales; solo responde al Coordinador.

Gracias a esto, el nodo es simple, sin estado y fácil de reemplazar o reiniciar.

```
Cliente ──► Coordinador (:8000) ──► Nodo 1 (:8001)  ./storage/
                                ──► Nodo 2 (:8002)  ./storage/
                                ──► Nodo 3 (:8003)  ./storage/
```

Los tres nodos ejecutan **exactamente el mismo código**; solo cambian el identificador
y el puerto, que se configuran con variables de entorno.

## 2. Estructura

```
node/
├── __init__.py   # Marca la carpeta como paquete Python
├── main.py       # Crea la app FastAPI y la arranca
└── router.py     # Endpoints internos: /health, /store, /retrieve
storage/          # Aquí se guardan los archivos reales (no se sube a Git)
```

## 3. Cómo funciona

### Guardar un archivo (`POST /store`)

1. El Coordinador envía el archivo como `multipart/form-data`.
2. El nodo valida el nombre (rechaza rutas como `../../algo`).
3. Lee el archivo **por bloques de 1 MB** y lo escribe en un archivo temporal dentro de `storage/`,
   calculando a la vez su tamaño y su hash SHA-256.
4. Fuerza la escritura a disco y **renombra** el temporal al nombre final (operación atómica).
5. Responde `201 Created` con el nombre, el tamaño y el `sha256`.

Si algo falla a mitad de camino, el temporal se elimina: nunca queda un archivo a medias
que parezca válido.

### Devolver un archivo (`GET /retrieve/{filename}`)

1. Valida el nombre.
2. Si el archivo existe en `storage/`, lo envía como flujo de bytes.
3. Si no existe, responde `404`. El Coordinador usa esa respuesta (o un timeout) para decidir
   que debe consultar la réplica en otro nodo.

### Verificar que está vivo (`GET /health`)

Responde `200` con su identificador y la cantidad de archivos que guarda. Si el proceso está
detenido, la conexión simplemente falla; así el Coordinador detecta que el nodo cayó.

## 4. Requisitos e instalación

- Python 3.9 o superior
- Tailscale instalado y conectado a la red del equipo (para pruebas entre máquinas)

Desde la raíz del proyecto:

```bash
python -m venv venv
# Linux / Mac
source venv/bin/activate
# Windows
venv\Scripts\activate

pip install fastapi uvicorn python-multipart
```

> `python-multipart` es obligatorio: sin él, `POST /store` falla al recibir archivos.

## 5. Configuración

Se hace con variables de entorno:

| Variable      | Por defecto | Descripción                                                  |
|---------------|-------------|--------------------------------------------------------------|
| `NODE_ID`     | `node1`     | Identificador del nodo (`node1`, `node2`, `node3`)           |
| `NODE_PORT`   | `8001`      | Puerto donde escucha                                         |
| `NODE_HOST`   | `0.0.0.0`   | Interfaz de red; puede ser la IP de Tailscale (`100.x.x.x`)  |
| `STORAGE_DIR` | `./storage` | Carpeta donde se guardan los archivos                        |

Asignación de nodos:

| Integrante | Rol    | `NODE_ID` | `NODE_PORT` |
|------------|--------|-----------|-------------|
| 1          | Nodo 1 | `node1`   | `8001`      |
| 2          | Nodo 2 | `node2`   | `8002`      |
| 3          | Nodo 3 | `node3`   | `8003`      |

## 6. Ejecución

Siempre desde la **raíz del proyecto**, no desde dentro de `node/`.

**Linux / Mac**
```bash
NODE_ID=node1 NODE_PORT=8001 python -m node.main
```

**Windows (PowerShell)**
```powershell
$env:NODE_ID="node1"; $env:NODE_PORT="8001"; python -m node.main
```

**Alternativa con uvicorn**
```bash
NODE_ID=node1 uvicorn node.main:app --host 0.0.0.0 --port 8001
```

Si todo va bien verás `Uvicorn running on http://0.0.0.0:8001`.

> **Firewall:** la primera vez, Windows puede preguntar si permite a Python recibir conexiones.
> Acepta, o abre manualmente el puerto del nodo (8001, 8002 u 8003).

## 7. API interna

Estos endpoints están pensados para ser usados **solo por el Coordinador**.

### `GET /health`
```json
{ "status": "ok", "node_id": "node1", "files_stored": 3 }
```

### `POST /store`
`multipart/form-data` con:

| Campo      | Tipo            | Obligatorio | Descripción                                   |
|------------|-----------------|-------------|-----------------------------------------------|
| `file`     | archivo binario | Sí          | Contenido del archivo                         |
| `filename` | texto           | No          | Nombre lógico; si falta se usa el del archivo |

Respuesta `201 Created`:
```json
{
  "status": "stored",
  "node_id": "node1",
  "filename": "prueba.txt",
  "size_bytes": 16,
  "sha256": "ab12..."
}
```

### `GET /retrieve/{filename}`
Devuelve los bytes del archivo (`application/octet-stream`).
`404` si no existe en este nodo; `400` si el nombre es inválido.

### Documentación interactiva
Con el nodo corriendo, abre `http://localhost:8001/docs` para probar los endpoints desde el navegador.

## 8. Pruebas

Mientras el Coordinador no exista (o para aislar fallos), se puede probar el nodo de forma
independiente. En Windows PowerShell usa `curl.exe` en lugar de `curl`.

### Pruebas manuales

```bash
echo "hola mini drive" > prueba.txt

curl http://localhost:8001/health                         # 200 + node_id
curl -F "file=@prueba.txt" http://localhost:8001/store    # 201 + sha256
curl http://localhost:8001/retrieve/prueba.txt            # "hola mini drive"
curl -i http://localhost:8001/retrieve/noexiste.txt       # 404
```

### Prueba automática (simula al Coordinador)

Requiere `pip install httpx`.

```python
import httpx, hashlib

NODE = "http://localhost:8001"   # cambiar por la IP de Tailscale para probar en red
data = b"contenido de prueba" * 1000

r = httpx.post(f"{NODE}/store", files={"file": ("demo.bin", data)}, data={"filename": "demo.bin"})
print(r.status_code, r.json())
assert r.json()["sha256"] == hashlib.sha256(data).hexdigest()

r = httpx.get(f"{NODE}/retrieve/demo.bin")
assert r.content == data
print("OK: ida y vuelta íntegra")
```

### Lista de verificación

| Prueba                                   | Resultado esperado                               |
|------------------------------------------|--------------------------------------------------|
| `GET /health`                            | 200 con el `node_id` correcto                    |
| `POST /store`                            | 201 y el archivo aparece en `storage/`           |
| `sha256` devuelto                        | Coincide con el hash local del archivo           |
| `GET /retrieve` de archivo existente     | Contenido idéntico al subido                     |
| `GET /retrieve` de archivo inexistente   | 404                                              |
| Mismo nombre subido dos veces            | Se sobrescribe sin error                         |
| `curl --path-as-is -i http://localhost:8001/retrieve/../main.py` | 400 o 404, nunca el contenido |
| Archivo grande (100 MB o más)            | Funciona sin disparar el uso de RAM              |

Para generar un archivo grande: `head -c 100M /dev/urandom > grande.bin` (Linux/Mac) o
`fsutil file createnew grande.bin 104857600` (Windows).

### Relación con el plan de pruebas del proyecto

- **P01 – Conectividad:** con Tailscale activo, otro integrante ejecuta
  `curl http://<IP-100.x.x.x>:<PUERTO>/health` y debe recibir 200. Si no responde, revisa el
  firewall y que `NODE_HOST` no sea `127.0.0.1`.
- **P02 – Distribución y rotación:** tras subir tres archivos desde el Coordinador, cada nodo
  debe mostrar en `/health` un `files_stored` consistente con el Round-Robin y sus réplicas.
- **P03 – Verificación física:** mostrar el contenido de la carpeta `storage/` del nodo.
- **P04 – Caída de nodo:** presionar `Ctrl+C` en la terminal del nodo. `/health` pasa a
  "connection refused", lo que activa el failover del Coordinador hacia la réplica. Al reiniciar
  el nodo, los archivos siguen en `storage/`.
- **P05 – Caída del Coordinador:** los nodos siguen vivos y sus archivos intactos; se puede
  demostrar con `curl` directo a `/health` y `/retrieve/...`.

## 9. Decisiones de diseño

- **Escritura atómica:** temporal + renombrado, para no dejar archivos corruptos si se corta la conexión.
- **Lectura por bloques (1 MB):** permite manejar archivos grandes sin cargarlos completos en memoria.
- **Validación de nombres:** se rechazan `/`, `\`, `..` y nombres que empiezan con `.`
  (evita *path traversal* y acceso a archivos temporales).
- **Hash SHA-256 en la respuesta:** el Coordinador puede comprobar que lo guardado es idéntico a lo enviado.
- **Sobrescritura:** subir un nombre existente reemplaza el archivo. Si el equipo prefiere rechazarlo,
  basta devolver `409 Conflict` en `router.py`.
- **Nodo sin estado:** toda la lógica (Round-Robin, replicación, failover) vive en el Coordinador.

## 10. Contrato con el Coordinador

El Coordinador debe:

1. Enviar `POST /store` con el campo de archivo llamado exactamente **`file`** (y opcionalmente `filename`).
2. Considerar la subida exitosa solo si recibe **201**.
3. Comparar el `sha256` devuelto con el calculado antes de enviar.
4. Tratar un timeout, un error de conexión o una respuesta distinta de 200 en `/health` como nodo caído.
5. Tratar un `404` en `/retrieve` como "este nodo no tiene el archivo" y probar con la réplica.

## 11. Limitaciones conocidas

- **Sin autenticación:** cualquier equipo dentro de la red Tailscale puede llamar al nodo. Es
  aceptable para este proyecto porque la red es privada, pero no para producción.
- **Sin cifrado de aplicación:** el tráfico es HTTP; Tailscale ya cifra el transporte entre equipos.
- **Sin borrado ni listado:** el contrato actual solo exige guardar y recuperar.
- **Sin control de espacio en disco:** si el disco se llena, `/store` responde 500.

## 13. `.gitignore` recomendado

```
venv/
__pycache__/
storage/*
!storage/.gitkeep
```
