# Análisis y simplificación según la guía

## Fuentes y criterio

Este análisis utiliza:

1. **Taller Parcial – Laboratorio Sistemas Distribuidos, octubre de 2026**, UTP, 5 páginas: requisitos del ejercicio y evidencias de evaluación.
2. **Programa IS893 – Sistemas Distribuidos**, UTP, 5 páginas: objetivos y contenidos de la asignatura.

El PDF de Laboratorio de Software sobre tiquetes aéreos se excluye: fue adjuntado por error y no define este proyecto.

La guía pide un sistema pequeño, distribuido entre máquinas independientes, que preste un servicio único y demuestre distribución, replicación y recuperación desde una réplica. El usuario indicó que se trata del primer laboratorio de un grupo de cuatro personas. El programa incluye temas avanzados durante el semestre, pero eso no los convierte en requisitos de esta primera entrega.

## Relación con los contenidos de la asignatura

| Contenido del programa | Aplicación en el proyecto |
| --- | --- |
| Unidad 1: objetivos, ventajas, desventajas y diseño de sistemas distribuidos | Compartir almacenamiento, separar responsabilidades, explicar fallos y el coste de la coordinación. |
| Unidad 2: comunicación y modelo cliente-servidor | Peticiones HTTP entre cliente, coordinador y nodos. |
| Unidad 3: exclusión mutua y bloqueos | Solo se usa un bloqueo local sencillo para proteger las subidas de un proceso. No se implementa un algoritmo de exclusión mutua distribuida. |
| Unidades 5 y 6: archivos distribuidos y servicios web | Contexto del servicio elegido; la guía permite expresamente almacenamiento y HTTP/REST. No se intenta implementar NFS ni todo el contenido de esas unidades. |
| Unidad 7: casos de estudio | Los clústeres y otras arquitecturas se pueden discutir como contexto o trabajo futuro; no se añaden a esta versión. |

No se presupone que el grupo haya cursado todas esas unidades. La sustentación debe concentrarse en la arquitectura, el recorrido de una petición y lo observado durante los fallos.

## Requisitos explícitos y forma de comprobarlos

| Guía | Requisito | Implementación o evidencia |
| --- | --- | --- |
| 1–4 | Máquinas independientes, IP distintas y colaboración por red | Un coordinador y tres nodos en cuatro computadores. La prueba local no certifica este requisito. |
| 5–6 | Tecnología justificable y red entre hosts | Python, FastAPI, HTTP/REST y LAN/Tailscale. No se exige Docker, RPC ni mensajería. |
| 7 | Propósito, responsabilidades, comunicación, datos, distribución, replicación y fallos | README y decisiones descritas en este documento. |
| 8 | Inventario de infraestructura | Tabla de cuatro hosts en DEMOSTRACION.md, pendiente de verificar y completar en el despliegue. |
| 9 | Conectividad entre hosts y comunicación entre procesos | Ping como comprobación auxiliar; llamadas HTTP a /health y script de conectividad. |
| 10 | Un servicio visible para el usuario | El cliente sube, lista y descarga desde el coordinador; no selecciona nodos. |
| 11 | Archivos realmente guardados en los nodos | /files muestra storage_key y ubicaciones; en cada host se muestra el objeto del disco y su SHA-256. |
| 12 | Uso de varios nodos y al menos una réplica | Distribución por turnos; dos copias de cada subida confirmada. |
| 13 | Recuperación tras detener un nodo con una copia | La descarga intenta principal y réplica; X-Storage-Node identifica cuál respondió. |
| 14 | Detener y analizar el fallo del coordinador | Se verifica que desaparece la API pública y los nodos conservan sus objetos. No se exige resolver ese punto único de fallo. |
| 15–16 | Evidencias y secuencia de demostración | Guion y lista de evidencias en DEMOSTRACION.md; capturas y resultados reales deben obtenerse en los equipos. |
| 17 | Explicar por qué es un sistema distribuido | Cooperación por HTTP, ubicaciones transparentes y redundancia utilizada por el servicio. |
| 18 | Mostrar IP, objetos físicos, fallos y discutir cien nodos | Incluido en el guion de sustentación. |

La implementación y las pruebas automatizadas cubren el comportamiento del programa. **La entrega no puede declararse completamente demostrada hasta ejecutar el guion en los cuatro hosts reales.**

## Qué excedía este laboratorio

La revisión anterior intentaba resolver fallos de un servicio con un alcance mayor. Eso introdujo complejidad que no era necesaria para el primer laboratorio:

| Antes | Ahora | Justificación y consecuencia |
| --- | --- | --- |
| SQLite con tablas de archivos, reservas, metadatos y basura | JSON con archivos publicados y turno de distribución | Basta para un coordinador y un conjunto pequeño de archivos. Escritura con temporal; fallo explícito si el JSON está dañado. |
| Actualizar nombres existentes con nuevas versiones | Rechazar duplicados con 409 | La guía no exige edición ni versiones. Evita tener que coordinar la sustitución y retirada de versiones anteriores. |
| Reservas durables, vencimientos y seguimiento de operaciones abandonadas | Una subida a la vez en el proceso coordinador | Simplifica el flujo; una caída puede dejar objetos sin registrar. No se promete recuperación transaccional de operaciones distribuidas. |
| Cola de basura, mantenimiento periódico y endpoint de limpieza | Sin limpieza automática | Los archivos publicados permanecen inmutables. Se documentan las copias sobrantes de intentos fallidos. |
| .locks, .deleted y bloqueos entre procesos | Bloqueo local en memoria y una instancia por directorio | No se soportan varios procesos sobre el mismo almacenamiento. Se mantienen los datos y la identidad .node.json de instalaciones anteriores. |
| /integrity y /repair | Verificar el hash durante la descarga y usar la segunda copia | Satisface recuperación desde réplica; reconstruir redundancia no es un requisito explícito. |
| Transferencias paralelas, temporales de descarga y gestión avanzada de cancelación | Solicitudes secuenciales y archivos pequeños en memoria | El flujo puede explicarse y seguirse paso a paso. Reduce rendimiento y tamaño práctico admitido. |
| Middleware propio de límites y múltiples variables de mantenimiento | Un límite de archivo de 10 MiB y tiempos HTTP | Protecciones básicas para la demostración, sin prometer protección frente a toda forma de sobrecarga HTTP. |
| requirements.txt y requirements.lock | Un requirements.txt con cuatro bibliotecas directas fijadas | Instalación sencilla. Las dependencias transitivas ya no están completamente fijadas. |
| Respaldo de SQLite durante la operación | Copiar el JSON con el coordinador detenido | Procedimiento suficiente para este alcance. La exportación SQLite→JSON se conserva solo para actualizar una instalación anterior. |

## Protecciones que sí se conservan

- **Dos confirmaciones antes de publicar:** una sola copia no basta para responder que el archivo fue guardado.
- **Tamaño y SHA-256:** se comprueban las respuestas de almacenamiento y el contenido descargado.
- **Identificador físico por intento:** un intento fallido no reutiliza el archivo de otro intento. Se mantiene storage_key para localizarlo en las evidencias.
- **Nombre visible único:** la segunda subida del mismo nombre devuelve 409 y conserva el archivo anterior.
- **Temporal y sustitución local:** el catálogo o el objeto final se sustituyen cuando la escritura termina. Esto no constituye una transacción entre cuatro equipos.
- **Validación de nombres y codificación de URL:** evita rutas fuera de storage y permite nombres con Unicode, espacios, # y %.
- **Identidad del directorio:** .node.json contiene NODE_ID y un identificador de almacenamiento. Evita confundir dos URL con dos almacenamientos distintos; no acredita que estén en computadores físicos diferentes.
- **Errores explícitos:** no se presenta como guardada una subida incompleta ni se reinicia un catálogo dañado silenciosamente.

Estas protecciones acompañan la demostración sin convertir el proyecto en un sistema de alta disponibilidad.

## Funcionamiento que el grupo debe poder explicar

**Subida:** recibir archivo → rechazar nombre repetido → leer dentro del límite → elegir candidatos por turnos → enviar una copia y verificarla → enviar la segunda y verificarla → guardar metadatos en JSON → responder 201.

**Descarga:** buscar el nombre en JSON → pedir el objeto al principal → verificar tamaño y hash → si falla, pedirlo a la réplica → entregar bytes e indicar el nodo en X-Storage-Node. Si ambas fallan, responder 503.

**Fallo del coordinador:** la URL única deja de prestar el servicio. Los nodos siguen siendo procesos independientes y conservan las copias. El acceso directo requiere conocer la IP y el identificador físico, por lo que pierde la transparencia que ofrece el coordinador.

## Verificación y límites del resultado

Se ejecutaron **34 pruebas de regresión** con almacenamiento aislado y una prueba HTTP con cuatro procesos en puertos locales libres. Se comprobaron copias físicas, reparto, nodo alternativo, failover, corrupción, duplicados, concurrencia dentro de un proceso, JSON persistente y migración desde SQLite. La prueba HTTP también detuvo el coordinador y comprobó acceso directo a un nodo antes del reinicio.

Los avisos de deprecación y recursos internos de TestClient/AnyIO observados con Python 3.14 corresponden al entorno de pruebas existente; se conservan visibles. No se actualizaron las dependencias como parte de esta simplificación.

No se han ejecutado desde esta revisión pruebas en los otros computadores del grupo, ni se han registrado capturas de su red. No se mide rendimiento de cien nodos, no se simulan cortes de energía y no se implementa tolerancia a la pérdida de todas las copias.

Los análisis anteriores se conservan en DIAGNOSTICO.md, CORRECCIONES.md y LIMPIEZA.md como historial. El contrato vigente está en el README y en este documento.
