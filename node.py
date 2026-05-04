"""
node.py — Node base class and built-in archetypes.

To define custom behaviour, either:
  (a) Subclass Node and override proactive() / reactive()
  (b) Pass callables at construction:
        n = Node("x", proactive_fn=my_fn)
"""

from __future__ import annotations
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional
import random

if TYPE_CHECKING:
    from graph import Graph


class Node:
    """
    A simulation node with two behaviour hooks:

    proactive(graph)         — called every step unconditionally
    reactive(event, graph)   — called for each queued event

    Both can be overridden in subclasses *or* injected as callables.
    """

    def __init__(
        self,
        node_id: Optional[str] = None,
        label: str = "",
        color: str = "#4f8ef7",
        proactive_fn: Optional[Callable] = None,
        reactive_fn: Optional[Callable] = None,
        **state_kwargs,
    ):
        self.id: str = node_id or str(uuid.uuid4())[:6]
        self.label: str = label or self.id
        r = lambda: random.randint(0,255)
        self.color: str = '#%02X%02X%02X' % (r(),r(),r())
        self.state: Dict[str, Any] = dict(state_kwargs)

        self._inbox: List[Dict] = []
        self._log: List[str] = []

        # Allow injecting behaviour without subclassing
        if proactive_fn:
            self.proactive = lambda graph: proactive_fn(self, graph)
        if reactive_fn:
            self.reactive = lambda event, graph: reactive_fn(self, event, graph)

    # ------------------------------------------------------------------
    # Behaviour hooks — override in subclasses
    # ------------------------------------------------------------------

    def proactive(self, graph: "Graph") -> None:
        """Called once per simulation step."""
        pass

    def reactive(self, event: Dict, graph: "Graph") -> None:
        """Called for each event delivered to this node."""
        pass

    # ------------------------------------------------------------------
    # Internal plumbing
    # ------------------------------------------------------------------

    def deliver(self, event: Dict) -> None:
        """Queue an event for processing on the next step."""
        self._inbox.append(event)

    def step(self, graph: "Graph") -> None:
        """Execute one simulation step (called by Graph.step)."""
        self.proactive(graph)
        inbox, self._inbox = self._inbox, []
        for ev in inbox:
            self.reactive(ev, graph)

    def send(self, graph: "Graph", target_id: str, event_type: str, **payload) -> None:
        """Convenience: send an event from inside a behaviour hook."""
        graph.send_event(self.id, target_id, {"type": event_type, **payload})

    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log.append(f"[{ts}] {msg}")
        if len(self._log) > 300:
            self._log = self._log[-300:]

    def recent_log(self, n: int = 30) -> List[str]:
        return self._log[-n:]

    def to_dict(self) -> Dict:
        return {
            "id":    self.id,
            "label": self.label,
            "color": self.color,
            "state": dict(self.state),
            "log":   self.recent_log(),
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id!r} label={self.label!r}>"


# ======================================================================
# Built-in archetypes
# ======================================================================

class PeerSwapNode(Node):
    TYPE_NAME = "counter"
    COLOR = "#f7a24f"

    def __init__(self, node_id=None, label="", broadcast_every: int = 3, **kw):
        super().__init__(node_id, label or "Counter", color=self.COLOR, **kw)
        self.state.setdefault("count", 0)
        self.broadcast_every = broadcast_every

    def proactive(self, graph):
        pass

    def reactive(self, event, graph):
        self.log(f"← {event['type']} from {event.get('from','?')}")


class CounterNode(Node):
    """
    Increments a counter each step.
    Broadcasts a 'ping' event to all neighbours every `broadcast_every` steps.
    """

    TYPE_NAME = "counter"
    COLOR = "#f7a24f"

    def __init__(self, node_id=None, label="", broadcast_every: int = 3, **kw):
        super().__init__(node_id, label or "Counter", color=self.COLOR, **kw)
        self.state.setdefault("count", 0)
        self.broadcast_every = broadcast_every

    def proactive(self, graph):
        self.state["count"] += 1
        c = self.state["count"]
        self.log(f"tick → count={c}")
        if c % self.broadcast_every == 0:
            for nb in graph.neighbours(self.id):
                self.send(graph, nb, "ping", count=c)

    def reactive(self, event, graph):
        self.log(f"← {event['type']} from {event.get('from','?')}")


class RelayNode(Node):
    """
    Forwards every received event to all outgoing neighbours
    (except back to the sender).
    """

    TYPE_NAME = "relay"
    COLOR = "#6fcf7a"

    def __init__(self, node_id=None, label="", **kw):
        super().__init__(node_id, label or "Relay", color=self.COLOR, **kw)
        self.state.setdefault("forwarded", 0)

    def reactive(self, event, graph):
        self.state["forwarded"] += 1
        targets = [n for n in graph.neighbours(self.id) if n != event.get("from")]
        self.log(f"relay {event['type']} → {targets}")
        for nb in targets:
            self.send(graph, nb, event["type"], **{k: v for k, v in event.items()
                                                    if k not in ("from", "type")})


class SinkNode(Node):
    """
    Absorbs all events; does nothing proactively.
    Useful as a terminal node to observe traffic.
    """

    TYPE_NAME = "sink"
    COLOR = "#cf6f6f"

    def __init__(self, node_id=None, label="", **kw):
        super().__init__(node_id, label or "Sink", color=self.COLOR, **kw)
        self.state.setdefault("received", 0)

    def reactive(self, event, graph):
        self.state["received"] += 1
        self.log(f"absorbed {event['type']} from {event.get('from','?')}")


class RandomWalkerNode(Node):
    """
    Each step, sends a 'walk' token to a randomly chosen neighbour.
    Tracks how many tokens it has seen pass through.
    """

    TYPE_NAME = "walker"
    COLOR = "#b06fcf"

    def __init__(self, node_id=None, label="", **kw):
        import random as _r
        super().__init__(node_id, label or "Walker", color=self.COLOR, **kw)
        self.state.setdefault("tokens_seen", 0)
        self._rng = _r.Random()

    def proactive(self, graph):
        nbs = graph.neighbours(self.id)
        if nbs:
            target = self._rng.choice(nbs)
            self.send(graph, target, "walk", hops=0)
            self.log(f"walk → {target}")

    def reactive(self, event, graph):
        if event["type"] == "walk":
            self.state["tokens_seen"] += 1
            hops = event.get("hops", 0) + 1
            self.log(f"walk token received (hop {hops})")
            nbs = [n for n in graph.neighbours(self.id) if n != event.get("from")]
            if nbs:
                target = self._rng.choice(nbs)
                self.send(graph, target, "walk", hops=hops)


# Registry used by the UI when creating nodes by type name
NODE_REGISTRY: Dict[str, type] = {
    "base":    Node,
    "counter": CounterNode,
    "relay":   RelayNode,
    "sink":    SinkNode,
    "walker":  RandomWalkerNode,
}