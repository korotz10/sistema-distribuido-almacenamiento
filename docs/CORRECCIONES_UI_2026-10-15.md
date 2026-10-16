**Correcciones de integración y comportamiento — 15 de octubre de 2026**

Se corrigieron las regresiones identificadas sobre `a942ed8`. El coordinador vuelve a usar un contrato compatible con los nodos, el panel verifica copias físicas y las eliminaciones incompletas permanecen registradas para reintentarlas.

| Problema | Cambio aplicado |
| --- | --- |
| El coordinador no importaba; contratos síncronos/asíncronos mezclados | Transporte síncrono para subir, descargar y eliminar; `NodeError`, comprobación de identidad, tamaño y SHA-256 restaurados. Se retiraron el catálogo creado al importar y el round-robin incompatible. |
| `/files` omitía el identificador físico | `FileInfo` vuelve a incluir `storage_key`; expone también el estado de eliminación y las identidades guardadas para nuevas subidas. Se retiraron modelos sin uso que describían contratos antiguos. |
| El panel confundía catálogo con inventario | El nodo ofrece `GET /files` con claves físicas, tamaños y hashes calculados desde disco. El coordinador consulta los tres nodos en paralelo y conserva los objetos no asignados. El panel compara clave, tamaño, hash e identidad y distingue ausente, corrupto y sin verificar. Un mismo almacenamiento no cuenta como dos copias. |
| Una eliminación fallida ocultaba cambios parciales | Se persiste `DELETING` antes de borrar. Cada intento solicita la eliminación a ambos nodos y solo retira el catálogo cuando recibe las dos confirmaciones válidas. `deleted_from` en la respuesta describe ese intento, no un registro persistente de progreso. |
| Cualquier HTTP 200 se aceptaba como eliminación | Se validan nodo, identidad del almacenamiento, clave y estado `deleted`/`absent`; las respuestas inválidas dejan la operación pendiente. En registros nuevos se exige la identidad original. Los registros heredados sin identidades conservan compatibilidad, pero no permiten acreditar retrospectivamente qué directorio existía al subir. |
| Refrescos solapados y falsas desconexiones | Una sola actualización en vuelo; las consultas manuales pendientes se agrupan. Las consultas fallidas no reutilizan inventarios antiguos como prueba de integridad. El panel diferencia fallo parcial de falta de conexión y solo confirma una consulta cuando termina correctamente. |
| Esperas acumuladas y sondeos sin cancelación | `NodeMonitor` consulta bajo demanda, comparte una ronda entre clientes simultáneos, usa HTTP asíncrono con límite total y cancela/espera las tareas al cerrar. No hay pool de hilos, sondeo periódico ni caché de salud en el servidor. El inventario tiene su propio plazo y comparte consultas simultáneas. |
| Pruebas y documentación antiguas | Se actualizaron los contratos esperados, las pruebas HTTP, README, plantilla de entorno y guía de demostración. Se añadió un workflow sencillo para Windows/Python 3.14, más pruebas del JavaScript con Node.js. |

**Semántica de eliminación**

Una solicitud aceptada para eliminar es irreversible y puede completarse parcialmente. El archivo queda visible como **Eliminación pendiente** y no permite nuevas descargas ni reutilizar el nombre hasta finalizar. Un `503` puede significar que ya se borró alguna copia; no significa que el archivo haya quedado intacto.

Si no se puede persistir la intención, no se contacta a los nodos para borrar. Si se pierde una confirmación o falla la retirada final del registro, el reintento consulta de nuevo ambos nodos; su eliminación es idempotente. Esto requiere que ambos estén disponibles para completar un intento, aunque ya hubieran borrado sus copias. No se implementa una transacción atómica distribuida ni eliminación automática en segundo plano. Los registros con `deleted_from` creados por la primera corrección siguen siendo legibles, pero ese campo ya no se utiliza para omitir consultas.

**Revisión de complejidad para el laboratorio**

No todas las medidas de la primera corrección eran imprescindibles. Se revisaron contra el alcance académico documentado y se redujeron las siguientes:

| Decisión | Justificación para esta actividad |
| --- | --- |
| Retirar el sondeo periódico del servidor, su caché y `PROBE_INTERVAL` | La interfaz ya consulta cada cinco segundos después de terminar la consulta anterior. Una segunda tarea periódica duplicaba trabajo y añadía coordinación y cierre de recursos. Si no hay consultas, no se monitoriza; las fechas describen observaciones, no disponibilidad continua. |
| Retirar confirmaciones de borrado persistidas por nodo | Basta guardar un estado pendiente y repetir los dos borrados idempotentes. Se elimina un método del catálogo y las escrituras intermedias, a cambio de necesitar ambos nodos disponibles para terminar el reintento. |
| Reducir CI de cuatro combinaciones a Windows/Python 3.14 | Se conserva una comprobación automática del entorno local usado, sin exigir mantenimiento de una matriz de plataformas para el laboratorio. |
| Conservar contratos coherentes, dos copias, hashes, inventario físico y refrescos ordenados | Corrigen fallos reproducidos que impedirían o falsearían la demostración. No son infraestructura adicional. |
| Conservar un estado de eliminación pendiente y validar respuestas | Eliminar ya era una función de la interfaz. Estas comprobaciones mínimas evitan afirmar que un archivo está intacto o eliminado cuando no se sabe. La guía no obliga a ofrecer borrado, pero retirarlo cambiaría una función existente. |
| Conservar las pruebas y el historial del diagnóstico | No añaden componentes al sistema desplegado. Los reproductores históricos quedan en `docs/`, separados de las pruebas vigentes. Node.js se usa únicamente para verificar el JavaScript, no para ejecutar el servicio. |

La explicación para la sustentación sigue siendo: **subir → confirmar dos copias → registrar; descargar → probar principal y réplica; consultar estado → preguntar a los nodos; eliminar → marcar pendiente → solicitar los dos borrados → retirar el registro**. No se añadieron bases de datos nuevas, colas, reintentos automáticos, procesos de reparación ni coordinación entre varios coordinadores.

**Validación local**

- 48 pruebas Python correctas, incluyendo las 34 regresiones previas y nuevas comprobaciones de inventario, identidad, eliminación parcial, fallos al persistir, confirmaciones inválidas/perdidas, reinicio, sondeo concurrente y cancelación.
- 10 pruebas del JavaScript correctas: integridad real, almacenamiento sustituido/duplicado, errores parciales, inventario obsoleto, desconexión, refrescos simultáneos, consulta forzada, presentación de eliminación pendiente y plazo HTTP.
- Prueba HTTP con cuatro procesos Uvicorn correcta: subida de más de 3 MiB, Unicode, copias físicas, hashes, failover, nodo alternativo, caída/reinicio del coordinador, servicio de la interfaz, inventario y eliminación parcial con reintento.
- `git diff --check` sin errores. Se conserva el HTML y no se modificaron datos de usuario ni catálogos reales.

El entorno local usa Windows, Python 3.14.2 y las cuatro dependencias directas existentes. No se actualizaron versiones. Continúan los avisos de deprecación y recursos internos de TestClient/AnyIO observados anteriormente; las pruebas funcionales pasan. El workflow queda preparado para ejecutarse en GitHub, pero no se afirma que ya haya pasado allí. La comprobación JavaScript usa DOM/HTTP simulados y no reemplaza una revisión visual en navegadores reales.

**Despliegue verificado**

El equipo confirmó posteriormente que el coordinador y los tres nodos funcionan y se comunican correctamente en los cuatro equipos mediante Tailscale (15 de octubre de 2026). Las direcciones y roles de los equipos se registran en [DEMOSTRACION.md](DEMOSTRACION.md).

Los scripts bajo `docs/diagnostico-ui/` reproducen la versión histórica `a942ed8`; las verificaciones de la versión corregida están en `tests/`.

Comandos desde la raíz, con el entorno virtual activo:

```powershell
python -m unittest discover -s tests -v
python tests/smoke_http.py
node tests/test_ui.mjs
```
