**Nodo de almacenamiento, versión 2**

El nodo guarda objetos inmutables, calcula tamaño y SHA-256, permite su recuperación y procesa eliminaciones idempotentes. El coordinador conserva la relación entre el nombre visible y el identificador físico de cada versión.

Consulte la [guía de instalación, migración y configuración](../README.md) antes de actualizar un nodo con datos existentes.

| Método y ruta | Resultado |
| --- | --- |
| GET /health | Identidad del nodo y del directorio, cantidad de archivos, capacidad libre y prueba de escritura. |
| POST /store | Multipart con file y filename opcional; 201 con tamaño, hash e identidades. |
| GET /retrieve/{filename} | FileResponse del objeto; 404 si no existe o fue cancelado. |
| DELETE /objects/{filename} | Marca la eliminación y borra el objeto. Repetir la petición devuelve 204. |

Todas estas rutas requieren Authorization: Bearer NODE_API_KEY. La documentación /docs permite configurar la credencial mediante Authorize.

Una escritura usa un temporal, lectura por bloques, fsync y publicación bajo un bloqueo entre procesos. Repetir el mismo identificador con contenido idéntico es idempotente; con bytes distintos devuelve 409. Si el identificador fue eliminado, una escritura tardía devuelve 410. Las actualizaciones de un archivo visible siempre se realizan desde el coordinador con un identificador nuevo.

El directorio por defecto es ./storage en la raíz del proyecto. Si ejecuta varios nodos en un mismo equipo, use un STORAGE_DIR distinto para cada uno. La identidad persistente impide abrir el mismo directorio con otro NODE_ID.

El nodo necesita NODE_ID, NODE_PORT y NODE_API_KEY; NODE_HOST controla la interfaz de escucha. MAX_FILE_SIZE y MAX_CONCURRENT_TRANSFERS limitan los recursos. Mantenga el límite de tamaño coherente con el coordinador.

No borre los archivos ocultos de control para liberar espacio: las marcas de eliminación impiden que una petición retrasada vuelva a publicar una operación cancelada. /health comprueba el almacenamiento en ese instante; /integrity del coordinador comprueba los bytes de cada archivo.
