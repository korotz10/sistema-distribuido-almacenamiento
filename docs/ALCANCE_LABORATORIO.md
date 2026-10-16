# Análisis y simplificación según la guía

> Este documento registra la simplificación anterior a la interfaz. El alcance académico se mantiene; las incorporaciones posteriores (inventario físico, sondeo y eliminaciones pendientes) y las pruebas vigentes se describen en [CORRECCIONES_UI_2026-10-15.md](CORRECCIONES_UI_2026-10-15.md) y en el [README](../README.md).

## Fuentes y criterio

Este análisis utiliza:

1. **Taller Parcial – Laboratorio Sistemas Distribuidos, octubre de 2026**, UTP, 5 páginas: requisitos del ejercicio y evidencias de evaluación.
2. **Programa IS893 – Sistemas Distribuidos**, UTP, 5 páginas: objetivos y contenidos de la asignatura.

El PDF de Laboratorio de Software sobre tiquetes aéreos se excluye: fue adjuntado por error y no define este proyecto.

La guía pide un sistema distribuido entre máquinas independientes que preste un servicio único y demuestre distribución, replicación y recuperación desde una réplica. El proyecto se desarrolla como primer laboratorio de un grupo de cuatro personas.

## Relación con los contenidos de la asignatura

| Contenido del programa | Aplicación en el proyecto |
| --- | --- |
| Unidad 1: objetivos, ventajas, desventajas y diseño de sistemas distribuidos | Compartir almacenamiento, separar responsabilidades, explicar fallos y el coste de la coordinación. |
| Unidad 2: comunicación y modelo cliente-servidor | Peticiones HTTP entre cliente, coordinador y nodos. |
| Unidad 3: exclusión mutua y bloqueos | Solo se usa un bloqueo local sencillo para proteger las subidas de un proceso. No se implementa un algoritmo de exclusión mutua distribuida. |
| Unidades 5 y 6: archivos distribuidos y servicios web | Contexto del servicio elegido; la guía permite expresamente almacenamiento y HTTP/REST. No se intenta implementar NFS ni todo el contenido de esas unidades. |
| Unidad 7: casos de estudio | Los clústeres y otras arquitecturas se pueden discutir como contexto o trabajo futuro; no se añaden a esta versión. |

La relación entre el programa y el proyecto se centra en la arquitectura, el recorrido de las peticiones y el comportamiento ante fallos.

## Requisitos explícitos y forma de comprobarlos

| Guía | Requisito | Implementación o evidencia |
| --- | --- | --- |
| 1–4 | Máquinas independientes, IP distintas y colaboración por red | Un coordinador y tres nodos en cuatro computadores, comunicados mediante Tailscale. |
| 5–6 | Tecnología y comunicación entre hosts | Python, FastAPI, HTTP/REST y Tailscale. |
| 7 | Propósito, responsabilidades, comunicación, datos, distribución, replicación y fallos | README y decisiones descritas en este documento. |
| 8 | Inventario de infraestructura | Tabla de los cuatro hosts en DEMOSTRACION.md; el equipo verificó el despliegue en Tailscale el 15 de octubre de 2026. |
| 9 | Conectividad entre hosts y comunicación entre procesos | Ping como comprobación auxiliar; llamadas HTTP a /health y script de conectividad. |
| 10 | Un servicio visible para el usuario | El cliente sube, lista y descarga desde el coordinador; no selecciona nodos. |
| 11 | Archivos realmente guardados en los nodos | /files presenta las ubicaciones registradas en el catálogo; /nodes/files enumera objetos físicos, storage_key y SHA-256. |
| 12 | Uso de varios nodos y al menos una réplica | Distribución por turnos; dos copias de cada subida confirmada. |
| 13 | Recuperación tras detener un nodo con una copia | La descarga intenta principal y réplica; X-Storage-Node identifica cuál respondió. |
| 14 | Detener y analizar el fallo del coordinador | Al detener el coordinador, su API deja de responder y los nodos conservan sus objetos. |
| 15–16 | Evidencias y secuencia de demostración | Secuencia de demostración y registro de resultados en `DEMOSTRACION.md`. |
| 17 | Explicar por qué es un sistema distribuido | Cooperación por HTTP, ubicaciones transparentes y redundancia utilizada por el servicio. |
| 18 | Mostrar IP, objetos físicos y fallos | Incluido en el guion de sustentación. |

La implementación y las pruebas automatizadas cubren el comportamiento del programa. El equipo verificó la comunicación del coordinador con los tres nodos mediante Tailscale y el funcionamiento de los servicios en los cuatro equipos el 15 de octubre de 2026.

## Decisiones de implementación

La revisión anterior intentaba resolver fallos de un servicio con un alcance mayor. Eso introdujo complejidad que no era necesaria para el primer laboratorio:

| Área | Versión previa | Implementación actual |
| --- | --- | --- |
| Catálogo | SQLite con tablas de archivos, reservas, metadatos y basura | JSON con los metadatos publicados y el índice de distribución, escrito mediante un archivo temporal. |
| Nombres repetidos | Actualización de versiones existentes | Se rechaza el nombre duplicado con HTTP `409`. |
| Coordinación de subidas | Reservas durables, vencimientos y seguimiento de operaciones | Un bloqueo en el proceso coordinador serializa las subidas. |
| Eliminación de objetos incompletos | Cola de basura y tarea de mantenimiento | No existe una tarea de limpieza automática; una subida parcial puede dejar objetos en un nodo. |
| Bloqueos del nodo | Archivos de bloqueo y coordinación entre procesos | Bloqueo local en memoria y un proceso por directorio de almacenamiento. |
| Descarga e integridad | Endpoints de auditoría y reparación | Verificación del tamaño y SHA-256 durante la descarga, con recuperación desde la segunda copia. |
| Transferencias | Transferencias paralelas y temporales de descarga | Envío secuencial de las dos copias; el contenido se valida antes de responder al cliente. |
| Límites de transferencia | Middleware y configuración de mantenimiento | Tamaño máximo configurable, por defecto 10 MiB, y plazos HTTP para consultas de salud e inventario. |
| Dependencias | Archivos de dependencias y bloqueo | Un `requirements.txt` con las cuatro dependencias directas fijadas. |
| Catálogo heredado | Respaldo y operación sobre SQLite | Catálogo JSON; `scripts/migrate_catalog.py` exporta catálogos SQLite previos a JSON. |

## Comportamiento de archivos y nodos

- **Dos confirmaciones antes de publicar:** una sola copia no basta para responder que el archivo fue guardado.
- **Tamaño y SHA-256:** se comprueban las respuestas de almacenamiento y el contenido descargado.
- **Identificador físico por intento:** un intento fallido no reutiliza el archivo de otro intento. Se mantiene storage_key para localizarlo en las evidencias.
- **Nombre visible único:** la segunda subida del mismo nombre devuelve 409 y conserva el archivo anterior.
- **Temporal y sustitución local:** el catálogo y cada objeto se escriben en un archivo temporal antes de sustituir el destino.
- **Validación de nombres y codificación de URL:** evita rutas fuera de storage y permite nombres con Unicode, espacios, # y %.
- **Identidad del directorio:** `.node.json` contiene `NODE_ID` y un identificador de almacenamiento, que el coordinador consulta y verifica para cada nodo.
- **Errores explícitos:** no se presenta como guardada una subida incompleta ni se reinicia un catálogo dañado silenciosamente.

## Flujos implementados

**Subida:** recibir archivo → rechazar nombre repetido → leer dentro del límite → elegir candidatos por turnos → enviar una copia y verificarla → enviar la segunda y verificarla → guardar metadatos en JSON → responder 201.

**Descarga:** buscar el nombre en JSON → pedir el objeto al principal → verificar tamaño y hash → si falla, pedirlo a la réplica → entregar bytes e indicar el nodo en X-Storage-Node. Si ambas fallan, responder 503.

**Detención del coordinador:** la API pública deja de responder y los nodos conservan las copias. Al reiniciar el coordinador con el mismo catálogo, vuelven a estar disponibles las operaciones del servicio.

## Verificación del proyecto

La suite vigente contiene **51 pruebas Python** y **12 pruebas del JavaScript de la interfaz**. La prueba HTTP local inicia cuatro procesos en puertos libres y comprueba copias físicas, distribución, recuperación desde réplica, reinicio, inventario y que una eliminación se rechace sin borrar copias mientras uno de sus nodos está apagado. Las pruebas unitarias cubren además el estado pendiente y su reintento después de un fallo durante el borrado, la configuración CORS del frontend desplegado y la URL configurable de la API. También se comprueba que la interfaz notifique los fallos de descarga. El equipo verificó el funcionamiento distribuido del coordinador y los tres nodos sobre Tailscale el 15 de octubre de 2026.

Los diagnósticos de versiones anteriores se conservan en `DIAGNOSTICO.md`, `CORRECCIONES.md` y `LIMPIEZA.md` como historial. El contrato vigente se describe en el README y en este documento.
