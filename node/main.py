"""Punto de entrada del Nodo de Almacenamiento.

Ejemplos (desde la raíz del proyecto):
    NODE_ID=node1 NODE_PORT=8001 python -m node.main
    NODE_ID=node1 uvicorn node.main:app --host 0.0.0.0 --port 8001
"""
import os

import uvicorn
from fastapi import FastAPI

from node.router import NODE_ID, STORAGE_DIR, router

app = FastAPI(title=f"Mini Drive - Nodo {NODE_ID}")
app.include_router(router)


@app.get("/")
async def root():
    return {"node_id": NODE_ID, "storage": str(STORAGE_DIR)}


if __name__ == "__main__":
    uvicorn.run(
        "node.main:app",
        host=os.getenv("NODE_HOST", "0.0.0.0"),
        port=int(os.getenv("NODE_PORT", "8001")),
    )