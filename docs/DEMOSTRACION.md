# Demostración del laboratorio en cuatro equipos

Esta es una guía de ejecución y registro de evidencias. Las celdas pendientes deben completarse con observaciones reales; no son resultados ya obtenidos.

## 1. Inventario de infraestructura

Los responsables e IP prellenados proceden de la configuración anterior del proyecto. Confirmen su asignación y completen la IP del coordinador.

| Host | Responsable | Rol | IP | Puerto | Estado observado y fecha |
| --- | --- | --- | --- | --- | --- |
| A | Santiago — confirmar | Coordinador | **Por completar** | 8000 | Pendiente |
| B | Ivanna — confirmar | node1 | 100.121.209.117 — verificar | 8001 | Pendiente |
| C | Dayana — confirmar | node2 | 100.90.207.18 — verificar | 8002 | Pendiente |
| D | Victor — confirmar | node3 | 100.98.240.121 — verificar | 8003 | Pendiente |

Ajusten la tabla, el diagrama del README y NODE1_URL/NODE2_URL/NODE3_URL si cambia la red. Cada fila debe representar un computador independiente.

Cada integrante puede presentar la configuración y almacenamiento de su host. Todos deben poder explicar el recorrido completo de una subida y una descarga.

## 2. Comprobar IP y conectividad

En cada equipo, identifiquen la IP usada:

```powershell
ipconfig
# Si el despliegue utiliza Tailscale:
tailscale ip -4
```

Desde cada host prueben las IP de los demás, según la red y las reglas de ICMP:

```powershell
ping IP_DEL_OTRO_HOST
Test-NetConnection IP_DEL_NODO -Port PUERTO_DEL_NODO
```

Sustituyan los marcadores por los valores del inventario. Registren resultados y origen de cada prueba. Una falta de respuesta a ping puede deberse a ICMP; prueben también el puerto del servicio.

Arranquen los servicios siguiendo el README. En el coordinador, con las URL reales:

```powershell
$env:COORDINATOR_URL="http://IP_COORDINADOR:8000"
python scripts/test_connectivity.py
```

Desde otros equipos pueden consultar directamente:

```powershell
curl.exe http://IP_COORDINADOR:8000/health
curl.exe http://IP_NODO_1:8001/health
```

La evidencia debe mostrar comunicación HTTP entre procesos, además de las direcciones de los hosts.

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

Si el profesor detiene la réplica en vez del principal, la descarga seguirá usando el principal. Si detiene un nodo que no contiene ese archivo, esa descarga no cambia; elijan también un archivo afectado para mostrar el uso real de la redundancia.

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

**¿Es el coordinador un punto único de fallo?** Sí. Su proceso y catálogo son necesarios para el acceso transparente. Los respaldos y el reinicio ayudan a recuperar el servicio, pero no proporcionan alta disponibilidad.

**¿Cómo podría mejorarse ese punto único de fallo?** Como propuesta futura: un coordinador de respaldo con metadatos sincronizados, un mecanismo para decidir cuál atiende y una dirección de acceso que pueda cambiar al activo. Copiar el JSON y arrancar dos escritores independientes no resuelve la coordinación.

**¿Qué cambiaría con cien nodos?** La lista estática y los sondeos secuenciales tardarían más; harían falta descubrimiento o registro de nodos, consultas de salud eficientes, transferencias concurrentes, límites de recursos y redistribución. También habría que revisar el catálogo central y el tráfico que pasa por el coordinador. Son ampliaciones propuestas, no capacidades de esta versión.

**¿Por qué JSON y una subida a la vez?** El conjunto de datos y la carga del laboratorio son pequeños. Un flujo secuencial permite explicar la decisión de nodos, las dos confirmaciones y la publicación sin introducir coordinación entre varios escritores.

## 8. Evidencias que deben adjuntarse

| Evidencia | Estado |
| --- | --- |
| Inventario y diagrama con las cuatro IP verificadas | Pendiente del grupo |
| Conectividad entre hosts | Pendiente del grupo |
| Solicitudes y respuestas HTTP entre equipos | Pendiente del grupo |
| Objetos físicos y hashes en los dos hosts | Pendiente del grupo |
| Distribución de varios archivos entre los tres nodos | Pendiente del grupo |
| Nodo detenido, descarga correcta y X-Storage-Node de la réplica | Pendiente del grupo |
| Coordinador detenido, nodos activos y análisis de operaciones | Pendiente del grupo |
| Reinicio, resultados, decisiones, ventajas y limitaciones | Pendiente del grupo |

Las pruebas automatizadas del repositorio verifican el comportamiento localmente. No completen estas celdas con sus resultados: el profesor solicita evidencia del despliegue distribuido real.
