"""Arranque de un nodo: python -m node.main."""
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from common.config import NODE_ID, NODE_PORT, STORAGE_DIR
from node.router import NodeStorage, create_router


def create_app(directory=STORAGE_DIR, node_id=NODE_ID):
    app = FastAPI(title=f"Mini Drive - {node_id}")
    app.state.storage = NodeStorage(directory, node_id)
    app.include_router(create_router(app.state.storage))

    @app.exception_handler(OSError)
    def storage_error(request: Request, exc: OSError):
        return JSONResponse({"detail": "No se pudo acceder al almacenamiento del nodo"}, status_code=503)

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("NODE_HOST", "0.0.0.0"), port=NODE_PORT)
