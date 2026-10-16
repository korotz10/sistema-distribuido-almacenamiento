# Demostración del laboratorio en cuatro equipos

Esta guía organiza la demostración del sistema en los cuatro equipos y el registro de sus resultados. El equipo verificó el funcionamiento del coordinador y los tres nodos sobre Tailscale el 15 de octubre de 2026.

Antes de comenzar, actualicen el coordinador y los tres nodos a la misma versión y reinicien conservando el catálogo y `STORAGE_DIR`. La interfaz está en `/ui/`; `/files` incluye `storage_key` para identificar los objetos físicos. Después de detener un nodo, usen `/status` o **Consultar estado** para solicitar un sondeo nuevo.

El endpoint del coordinador `/files` devuelve el catálogo y las ubicaciones registradas. `/nodes/files` devuelve el inventario físico comunicado por los nodos y el tamaño y SHA-256 de cada objeto. Para observar una eliminación incompleta, utilicen un archivo de prueba, detengan uno de sus nodos asignados e intenten eliminarlo; el catálogo conserva el estado `DELETING` hasta que el borrado se complete.

## 1. Inventario de infraestructura

| Host | Responsable | Rol | IP | Puerto | Estado observado y fecha |
| --- | --- | --- | --- | --- | --- |
| A | Santiago | Coordinador | Dirección Tailscale del equipo A | 8000 | Operativo; conexión verificada, 15-oct-2026 |
| B | Ivanna | node1 | 100.121.209.117 | 8001 | Operativo; conexión verificada, 15-oct-2026 |
| C | Dayana | node2 | 100.90.207.18 | 8002 | Operativo; conexión verificada, 15-oct-2026 |
| D | Victor | node3 | 100.98.240.121 | 8003 | Operativo; conexión verificada, 15-oct-2026 |

La dirección del Host A corresponde al equipo que ejecuta el coordinador. Los tres nodos utilizan las direcciones indicadas en la tabla y en la configuración del coordinador.

## 2. Comprobar IP y conectividad

En cada equipo, la IP de Tailscale se consulta con:

```powershell
ipconfig
tailscale ip -4
```

La comunicación entre los cuatro equipos sobre Tailscale está verificada. Estas consultas permiten observar las direcciones y los puertos de los servicios:

```powershell
ping IP_DEL_OTRO_HOST
Test-NetConnection IP_DEL_NODO -Port PUERTO_DEL_NODO
```

Arranquen los servicios siguiendo el README. En el coordinador, la dirección utilizada por el script de conectividad se establece así:

```powershell
$env:COORDINATOR_URL="http://IP_COORDINADOR:8000"
python scripts/test_connectivity.py
```

Desde otros equipos pueden consultar directamente:

```powershell
curl.exe http://IP_COORDINADOR:8000/health
curl.exe http://IP_NODO_1:8001/health
```

El script consulta el estado HTTP del coordinador y de los tres nodos.

## 3. Subir archivos y comprobar distribución

Prepare tres archivos pequeños con **nombres nuevos**. Si el catálogo ya contiene archivos, el turno puede comenzar en otro nodo; anote las ubicaciones reales de las respuestas.

En el equipo cliente:

```powershell
$base="http://IP_COORDINADOR:8000"
curl.exe -F "file=@./prueba1.txt" "$base/upload"
curl.exe -F "file=@./prueba2.txt" "$base/upload"
curl.exe -F "file=@./prueba3.txt" "$base/upload"
curl.exe "$base/files"
```

Con todos los nodos activos y catálogo nuevo, se esperan los pares 1–2, 2–3 y 3–1. El usuario solo utiliza el coordinador; no elige esos pares.

Guarden las respuestas 201 y el listado. Anoten para un archivo:

| Dato | Valor observado |
| --- | --- |
| Nombre visible | Pendiente |
| storage_key | Pendiente |
| SHA-256 | Pendiente |
| Nodo principal | Pendiente |
| Nodo réplica | Pendiente |

## 4. Mostrar los archivos reales en ambos hosts

En cada uno de los dos nodos registrados, localicen el objeto dentro de su STORAGE_DIR:

```powershell
Get-Item "./storage/IDENTIFICADOR_FISICO"
Get-FileHash "./storage/IDENTIFICADOR_FISICO" -Algorithm SHA256
```

Sustituyan IDENTIFICADOR_FISICO por storage_key de /files. Si cambiaron STORAGE_DIR, usen esa ruta. El hash debe coincidir con el catálogo en ambos equipos; las diferencias de mayúsculas no cambian su valor.

Para un archivo de texto pueden mostrar también su contenido:

```powershell
Get-Content "./storage/IDENTIFICADOR_FISICO"
```

Obtengan evidencia de **ambos equipos**, identificando host, ruta y hash. Dos archivos dentro del mismo computador no demuestran independencia física.

## 5. Detener un nodo y recuperar desde la réplica

Antes del fallo:

```powershell
curl.exe -D "./antes.txt" -o "./descarga-antes.txt" "$base/download/prueba1.txt"
Get-Content "./antes.txt"
Get-FileHash "./descarga-antes.txt" -Algorithm SHA256
```

Detengan con Ctrl+C el proceso del nodo principal de ese archivo. Consulten estado y vuelvan a descargar:

```powershell
curl.exe "$base/status"
curl.exe -D "./despues.txt" -o "./descarga-despues.txt" "$base/download/prueba1.txt"
Get-Content "./despues.txt"
Get-FileHash "./descarga-despues.txt" -Algorithm SHA256
```

Se espera que el nodo detenido aparezca DOWN, la descarga responda 200 y X-Storage-Node cambie al identificador de la réplica. El contenido y el hash deben permanecer iguales.

Si se detiene la réplica, la descarga continúa desde el principal. Si se detiene un nodo que no contiene el archivo solicitado, esa descarga no cambia.

Reinicien el nodo antes del siguiente ejercicio para demostrar el fallo del coordinador con los tres nodos activos.

## 6. Detener el coordinador

Anoten previamente el storage_key del archivo y la IP de una copia. Detengan únicamente el coordinador y mantengan activos los nodos.

```powershell
curl.exe "$base/files"
curl.exe "$base/download/prueba1.txt"
curl.exe http://IP_NODO_CON_COPIA:PUERTO/health
curl.exe -o "./directo.txt" http://IP_NODO_CON_COPIA:PUERTO/retrieve/IDENTIFICADOR_FISICO
```

Se espera:

| Operación | Resultado |
| --- | --- |
| Subir, listar o descargar a través del coordinador detenido | No hay servicio HTTP disponible en esa URL. |
| Consultar la salud de un nodo activo | El nodo responde. |
| Recuperar directamente un objeto conocido | El nodo conserva y entrega los bytes. |
| Pedir por nombre visible sin conocer su ubicación | El servicio que resolvía ese nombre está detenido. |

Reinicien el coordinador con el mismo catálogo y vuelvan a listar y descargar. Esto demuestra persistencia después de un reinicio, **no conmutación automática a otro coordinador**.

## 7. Preguntas para la sustentación

**¿Por qué es un sistema distribuido?** Cuatro máquinas independientes colaboran por HTTP para prestar un único servicio. El coordinador oculta las ubicaciones y utiliza copias en otros hosts para atender fallos.

**¿Qué diferencia hay entre distribución y replicación?** Distribuir reparte archivos y trabajo entre nodos. Replicar guarda el mismo recurso en más de uno. El proyecto muestra ambas cosas.

**¿Qué aporta SHA-256?** Permite comparar el contenido recibido con el registrado. Tener un nodo activo no demuestra que sus archivos sigan correctos.

**¿Qué ocurre si solo queda un nodo disponible?** Las subidas nuevas fallan porque necesitan dos copias. Un archivo existente puede descargarse si una de sus dos copias está en ese nodo.

**¿Qué función cumple el coordinador durante una descarga?** Busca el nombre visible en el catálogo, consulta primero el nodo principal, verifica tamaño y SHA-256 y, si la copia no está disponible o no coincide, intenta la réplica.

**¿Cómo se asignan los nodos durante una subida?** El coordinador recorre los nodos en orden Round-Robin, verifica su salud e identidad y confirma secuencialmente dos copias antes de registrar el archivo.

## 8. Evidencias que deben adjuntarse

| Evidencia | Estado |
| --- | --- |
| Inventario de los cuatro hosts y sus direcciones Tailscale | Conectividad y servicios verificados el 15-oct-2026; la dirección del Host A se consulta en el equipo coordinador. |
| Comunicación HTTP del coordinador con los tres nodos | Verificada en el despliegue, 15-oct-2026. |
| Objetos físicos y hashes en sus dos ubicaciones | Registrar durante la demostración del archivo seleccionado. |
| Distribución entre los tres nodos | Registrar las respuestas de subida y `GET /files`. |
| Descarga con un nodo detenido | Registrar la respuesta y la cabecera `X-Storage-Node`. |
| Coordinador detenido y reiniciado | Registrar el estado de los nodos y el acceso posterior al catálogo. |
| Eliminación pendiente y reintento | Registrar las respuestas y el estado del catálogo. |

Las pruebas automatizadas comprueban la lógica del programa con almacenamiento temporal y procesos locales. Esta guía registra las observaciones correspondientes a la ejecución distribuida del equipo.
