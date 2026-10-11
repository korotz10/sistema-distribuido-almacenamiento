"""
Punto de entrada para ejecutar el Nodo de Almacenamiento.

Ejemplos de ejecución en terminal:
  - Linux/Mac:
      NODE_ID=node1 NODE_PORT=8001 python -m node.main

  - Windows (PowerShell):
      $env:NODE_ID="node1"; $env:NODE_PORT="8001"; python -m node.main
"""
import os
import uvicorn
from fastapi import FastAPI
from node.router import NODE_ID, NODE_PORT, STORAGE_DIR, router

# Inicializar aplicación FastAPI
app = FastAPI(
    title=f"Mini Drive - Nodo {NODE_ID}",
    description="Nodo de almacenamiento distribuido (Trabajador)",
    version="1.0.0",
)

# Incluir los endpoints del router (/health, /store, /retrieve)
app.include_router(router)


@app.get("/", tags=["Información"])
async def root():
    """Endpoint informativo de bienvenida al nodo."""
    return {
        "service": "Mini Drive - Nodo de Almacenamiento",
        "node_id": NODE_ID,
        "port": NODE_PORT,
        "storage_directory": str(STORAGE_DIR),
        "status": "online",
    }


if __name__ == "__main__":
    host = os.getenv("NODE_HOST", "0.0.0.0")
    port = int(os.getenv("NODE_PORT", "8001"))
    print(f"🚀 Iniciando Nodo '{NODE_ID}' en {host}:{port}...")
    uvicorn.run("node.main:app", host=host, port=port, reload=False)