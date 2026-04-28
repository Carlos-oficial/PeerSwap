"""
graph.py — Graph simulation engine.

Stores nodes and directed edges, handles event routing, and advances
the simulation one step at a time.
"""

from __future__ import annotations
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from node import Node, NODE_REGISTRY


class Graph:
    """
    Directed graph of Nodes.

    Key responsibilities:
      - Topology management (add/remove nodes and edges)
      - Event routing between nodes
      - Advancing the simulation (step)
      - Change notification (observers pattern for the UI)
    """

    def __init__(self):
        self.nodes: Dict[str, Node] = {}
        # edges stored as list of {"src": id, "dst": id}
        self.edges: List[Dict[str, str]] = []

        self.step_count: int = 0
        self._event_log: List[str] = []   # cross-node event history
        self._lock = threading.Lock()

        # Callbacks fired after each step (UI hooks in here)
        self._on_change: List[Callable] = []

    # ------------------------------------------------------------------
    # Observer
    # ------------------------------------------------------------------

    def on_change(self, fn: Callable) -> None:
        self._on_change.append(fn)

    def _notify(self) -> None:
        for fn in self._on_change:
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> None:
        with self._lock:
            self.nodes[node.id] = node
        self._notify()

    def remove_node(self, node_id: str) -> None:
        with self._lock:
            self.nodes.pop(node_id, None)
            self.edges = [e for e in self.edges
                          if e["src"] != node_id and e["dst"] != node_id]
        self._notify()

    def add_edge(self, src_id: str, dst_id: str) -> bool:
        """Add a directed edge; returns False if either node is missing or edge exists."""
        with self._lock:
            if src_id not in self.nodes or dst_id not in self.nodes:
                return False
            if any(e["src"] == src_id and e["dst"] == dst_id for e in self.edges):
                return False
            self.edges.append({"src": src_id, "dst": dst_id})
        self._notify()
        return True

    def remove_edge(self, src_id: str, dst_id: str) -> None:
        with self._lock:
            self.edges = [e for e in self.edges
                          if not (e["src"] == src_id and e["dst"] == dst_id)]
        self._notify()

    def has_edge(self, src_id: str, dst_id: str) -> bool:
        return any(e["src"] == src_id and e["dst"] == dst_id for e in self.edges)

    def neighbours(self, node_id: str) -> List[str]:
        """Outgoing neighbours of node_id."""
        return [e["dst"] for e in self.edges if e["src"] == node_id]

    def predecessors(self, node_id: str) -> List[str]:
        """Incoming neighbours of node_id."""
        return [e["src"] for e in self.edges if e["dst"] == node_id]

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send_event(self, from_id: str, to_id: str, event: Dict) -> None:
        """Route an event; called by nodes during their step."""
        event = dict(event)
        event["from"] = from_id
        if to_id in self.nodes:
            self.nodes[to_id].deliver(event)
            entry = (f"[step {self.step_count}] "
                     f"{from_id} → {to_id} : {event.get('type','?')}")
            self._event_log.append(entry)
            if len(self._event_log) > 500:
                self._event_log = self._event_log[-500:]

    def inject_event(self, to_id: str, event_type: str, **payload) -> bool:
        """Manually inject an event from the UI."""
        if to_id not in self.nodes:
            return False
        ev = {"type": event_type, "from": "__user__", **payload}
        self.nodes[to_id].deliver(ev)
        self._event_log.append(
            f"[step {self.step_count}] USER → {to_id} : {event_type}")
        return True

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def step(self, n: int = 1) -> None:
        """Advance the simulation by n steps."""
        with self._lock:
            for _ in range(n):
                self.step_count += 1
                for node in list(self.nodes.values()):
                    node.step(self)
        self._notify()

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    def create_node(self, type_name: str, node_id: str = None,
                    label: str = "", **kwargs) -> Optional[Node]:
        cls = NODE_REGISTRY.get(type_name, Node)
        node = cls(node_id=node_id, label=label or type_name, **kwargs)
        self.add_node(node)
        return node

    # ------------------------------------------------------------------
    # Serialisation (for display)
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "step":      self.step_count,
                "nodes":     [n.to_dict() for n in self.nodes.values()],
                "edges":     list(self.edges),
                "event_log": list(self._event_log[-60:]),
            }

    def recent_events(self, n: int = 60) -> List[str]:
        return self._event_log[-n:]