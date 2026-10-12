"""Servidor de almacenamiento. NODE_API_KEY es obligatorio."""
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.security import HTTPBearer
import uvicorn

from common.security import AccessMiddleware, validate_key
from node.router import NODE_ID, NODE_PORT, STORAGE_DIR, NodeStorage, create_router


def create_app(directory: Path = STORAGE_DIR, node_id: str = NODE_ID):
    @asynccontextmanager
    async def lifespan(app):
        validate_key("NODE_API_KEY")
        yield

    app = FastAPI(title=f"Mini Drive - Nodo {node_id}", version="2.0.0",
                  lifespan=lifespan, dependencies=[Depends(HTTPBearer(auto_error=False))])
    storage = NodeStorage(directory, node_id)
    app.state.storage = storage
    app.include_router(create_router(storage))
    app.add_middleware(AccessMiddleware, key_variable="NODE_API_KEY")

    @app.get("/")
    async def root():
        return {"service": "Mini Drive - Nodo", "node_id": node_id, "status": "online"}

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("node.main:app", host=os.getenv("NODE_HOST", "0.0.0.0"), port=NODE_PORT, reload=False)
