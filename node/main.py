"""Servidor de almacenamiento configurable mediante NODE_ID y NODE_PORT."""
import os
from pathlib import Path

from fastapi import FastAPI
import uvicorn

from common.limits import RequestLimitsMiddleware
from node.router import NODE_ID, NODE_PORT, STORAGE_DIR, NodeStorage, create_router


def create_app(directory: Path = STORAGE_DIR, node_id: str = NODE_ID):
    app = FastAPI(title=f"Mini Drive - Nodo {node_id}", version="2.0.0")
    storage = NodeStorage(directory, node_id)
    app.state.storage = storage
    app.include_router(create_router(storage))
    app.add_middleware(RequestLimitsMiddleware)

    @app.get("/")
    async def root():
        return {"service": "Mini Drive - Nodo", "node_id": node_id, "status": "online"}

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("node.main:app", host=os.getenv("NODE_HOST", "0.0.0.0"), port=NODE_PORT, reload=False)
