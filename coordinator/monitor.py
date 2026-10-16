"""Sondeo compartido: una ronda en vuelo, consultas cancelables y plazo total."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone

from coordinator import services


class NodeMonitor:
    def __init__(self, timeout, log_event):
        self.timeout = timeout
        self.log_event = log_event
        self.state = {}
        self.task = None

    async def snapshot(self):
        """Consultar bajo demanda; solo los clientes simultáneos comparten la ronda."""
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._probe())
        return await asyncio.shield(self.task)

    async def _probe(self):
        nodes = services.get_nodes()
        checks = await asyncio.gather(*(services.probe_node(n, self.timeout) for n in nodes))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = {}
        for node, (ready, details) in zip(nodes, checks):
            previous = self.state.get(node["id"], {})
            status = "UP" if ready else "DOWN"
            changed = previous.get("status") != status
            if changed:
                self.log_event("node", f"{node['id']} {'conectado' if ready else 'dejó de responder'}", node=node["id"])
            result[node["id"]] = {
                **node, "node_id": node["id"], "status": status, "details": details,
                "connected_since": (now if changed else previous.get("connected_since")) if ready else None,
                "last_seen": now if ready else previous.get("last_seen"),
                "down_since": None if ready else (now if changed else previous.get("down_since")),
                "checked_at": now,
            }
        self.state = result
        return list(result.values())

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
