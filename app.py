"""
app.py — Tkinter graph visualiser & simulation tool.

Layout
------
┌──────────────────────────────────────────────────────────────┐
│  Toolbar: Step | Play | Speed | Step counter                 │
├─────────────────────────────┬────────────────────────────────┤
│                             │  [Inspector / Add-node panel]  │
│   Canvas (force layout)     │  Node info + state             │
│                             ├────────────────────────────────┤
│                             │  Event log (scrolled text)     │
├─────────────────────────────┴────────────────────────────────┤
│  Status bar                                                  │
└──────────────────────────────────────────────────────────────┘

Interactions
------------
- Click a node         → select & inspect
- Drag a node          → reposition
- Right-click canvas   → context menu (add node)
- Right-click node     → context menu (remove, inject event, add edge)
- Toolbar              → step / auto-play / speed control
"""

from __future__ import annotations

import math
import random
import threading
import time
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from typing import Dict, List, Optional, Tuple

from graph import Graph
from node import NODE_REGISTRY, CounterNode, RelayNode, SinkNode, RandomWalkerNode


# ──────────────────────────────────────────────────────────────────────────────
# Constants & palette
# ──────────────────────────────────────────────────────────────────────────────

BG          = "#1a1b26"
PANEL_BG    = "#16213e"
ACCENT      = "#4f8ef7"
ACCENT2     = "#6fcf7a"
TEXT        = "#e0e0f0"
TEXT_DIM    = "#6b7080"
NODE_R      = 22          # node circle radius (px)
EDGE_COLOR  = "#3a4060"
EDGE_ACTIVE = "#f7a24f"   # colour flash for recently used edges
FONT_MONO   = ("Courier", 9)
FONT_UI     = ("TkDefaultFont", 9)
FONT_BOLD   = ("TkDefaultFont", 10, "bold")

STEP_INTERVALS = {   # label → ms between auto-steps
    "Slow":   800,
    "Normal": 300,
    "Fast":   80,
    "Turbo":  20,
}


# ──────────────────────────────────────────────────────────────────────────────
# Force-directed layout  (simple Fruchterman–Reingold-ish)
# ──────────────────────────────────────────────────────────────────────────────

class ForceLayout:
    """Iterative spring/repulsion layout for graph nodes."""

    def __init__(self, width: int, height: int):
        self.width  = width
        self.height = height
        self.pos: Dict[str, List[float]] = {}   # id → [x, y]
        self.vel: Dict[str, List[float]] = {}   # id → [vx, vy]

    def add_node(self, nid: str, x: float = None, y: float = None):
        if nid not in self.pos:
            self.pos[nid] = [
                x if x is not None else random.uniform(100, self.width  - 100),
                y if y is not None else random.uniform(100, self.height - 100),
            ]
            self.vel[nid] = [0.0, 0.0]

    def remove_node(self, nid: str):
        self.pos.pop(nid, None)
        self.vel[nid] = [0.0, 0.0]

    def sync(self, node_ids: List[str]):
        """Ensure layout knows about exactly the given nodes."""
        for nid in node_ids:
            self.add_node(nid)
        for nid in list(self.pos):
            if nid not in node_ids:
                self.remove_node(nid)

    def tick(self, edges: List[Dict], pinned: Dict[str, Tuple] = None,
             iterations: int = 1):
        pinned = pinned or {}
        ids = list(self.pos)
        if not ids:
            return

        k = math.sqrt(self.width * self.height / max(len(ids), 1)) * 0.6
        damping = 0.85

        for _ in range(iterations):
            force: Dict[str, List[float]] = {nid: [0.0, 0.0] for nid in ids}

            # Repulsion between every pair
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    dx = self.pos[a][0] - self.pos[b][0]
                    dy = self.pos[a][1] - self.pos[b][1]
                    d  = math.hypot(dx, dy) or 0.01
                    f  = (k * k) / d
                    force[a][0] += f * dx / d
                    force[a][1] += f * dy / d
                    force[b][0] -= f * dx / d
                    force[b][1] -= f * dy / d

            # Attraction along edges
            for e in edges:
                a, b = e["src"], e["dst"]
                if a not in self.pos or b not in self.pos:
                    continue
                dx = self.pos[b][0] - self.pos[a][0]
                dy = self.pos[b][1] - self.pos[a][1]
                d  = math.hypot(dx, dy) or 0.01
                f  = (d * d) / k
                force[a][0] += f * dx / d
                force[a][1] += f * dy / d
                force[b][0] -= f * dx / d
                force[b][1] -= f * dy / d

            # Integrate
            for nid in ids:
                if nid in pinned:
                    self.pos[nid] = list(pinned[nid])
                    self.vel[nid] = [0.0, 0.0]
                    continue
                self.vel[nid][0] = (self.vel[nid][0] + force[nid][0]) * damping
                self.vel[nid][1] = (self.vel[nid][1] + force[nid][1]) * damping
                # clamp
                speed = math.hypot(*self.vel[nid])
                if speed > 40:
                    self.vel[nid][0] = self.vel[nid][0] / speed * 40
                    self.vel[nid][1] = self.vel[nid][1] / speed * 40
                self.pos[nid][0] = max(NODE_R + 4, min(
                    self.width  - NODE_R - 4, self.pos[nid][0] + self.vel[nid][0]))
                self.pos[nid][1] = max(NODE_R + 4, min(
                    self.height - NODE_R - 4, self.pos[nid][1] + self.vel[nid][1]))


# ──────────────────────────────────────────────────────────────────────────────
# Main application window
# ──────────────────────────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self, graph: Graph):
        super().__init__()
        self.graph = graph

        self.title("Graph Simulation Tool")
        self.configure(bg=BG)
        self.geometry("1200x750")
        self.minsize(900, 600)

        # State
        self._selected: Optional[str] = None    # selected node id
        self._drag_node: Optional[str] = None
        self._drag_offset: Tuple[float, float] = (0, 0)
        self._edge_src: Optional[str] = None    # for interactive edge drawing
        self._pinned: Dict[str, Tuple] = {}      # manually dragged nodes

        self._playing   = False
        self._play_job  = None
        self._layout_job = None

        self._active_edges: Dict[Tuple[str,str], float] = {}  # edge → expiry time

        # Build UI
        self._build_styles()
        self._build_toolbar()
        self._build_main_area()
        self._build_statusbar()

        # Force layout
        self.layout = ForceLayout(800, 600)

        # Wire graph change notifications
        self.graph.on_change(self._schedule_redraw)

        # Seed initial layout
        snap = self.graph.snapshot()
        for n in snap["nodes"]:
            self.layout.add_node(n["id"])

        # Start layout ticker
        self._tick_layout()
        self._refresh_inspector()

    # ──────────────────────────────────────────────────────────────────
    # Style setup
    # ──────────────────────────────────────────────────────────────────

    def _build_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".",
            background=PANEL_BG, foreground=TEXT,
            fieldbackground=BG, troughcolor=BG,
            bordercolor=EDGE_COLOR, darkcolor=PANEL_BG, lightcolor=PANEL_BG,
            selectbackground=ACCENT, selectforeground="white",
            font=FONT_UI)

        style.configure("TButton",
            background="#252840", foreground=TEXT, relief="flat",
            padding=(8, 4), borderwidth=0)
        style.map("TButton",
            background=[("active", ACCENT), ("pressed", "#2a4aab")])

        style.configure("Accent.TButton",
            background=ACCENT, foreground="white", font=FONT_BOLD)
        style.map("Accent.TButton",
            background=[("active", "#6fa8fc"), ("pressed", "#2a4aab")])

        style.configure("TLabel",  background=PANEL_BG, foreground=TEXT)
        style.configure("Dim.TLabel", background=PANEL_BG, foreground=TEXT_DIM)
        style.configure("TFrame",  background=PANEL_BG)
        style.configure("TEntry",
            fieldbackground="#252840", foreground=TEXT, insertcolor=TEXT)
        style.configure("TCombobox",
            fieldbackground="#252840", foreground=TEXT)
        style.configure("TScale",
            background=PANEL_BG, troughcolor="#252840")
        style.configure("Sash", sashrelief="flat", sashwidth=4,
            background=EDGE_COLOR)

    # ──────────────────────────────────────────────────────────────────
    # Toolbar
    # ──────────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = tk.Frame(self, bg="#111220", height=44)
        tb.pack(side=tk.TOP, fill=tk.X)
        tb.pack_propagate(False)

        def sep():
            tk.Frame(tb, bg=EDGE_COLOR, width=1).pack(side=tk.LEFT,
                fill=tk.Y, padx=6, pady=8)

        # Step button
        ttk.Button(tb, text="▶  Step", style="Accent.TButton",
            command=self._do_step).pack(side=tk.LEFT, padx=(10, 2), pady=6)

        # Step N
        ttk.Button(tb, text="▶▶  Step ×10",
            command=lambda: self._do_step(10)).pack(side=tk.LEFT, padx=2, pady=6)

        sep()

        # Play / Pause
        self._play_btn_var = tk.StringVar(value="⏵  Play")
        ttk.Button(tb, textvariable=self._play_btn_var,
            command=self._toggle_play).pack(side=tk.LEFT, padx=2, pady=6)

        # Speed
        ttk.Label(tb, text="Speed:", background="#111220").pack(
            side=tk.LEFT, padx=(8, 2))
        self._speed_var = tk.StringVar(value="Normal")
        sp = ttk.Combobox(tb, textvariable=self._speed_var,
            values=list(STEP_INTERVALS), width=7, state="readonly")
        sp.pack(side=tk.LEFT, pady=6)

        sep()

        # Reset
        ttk.Button(tb, text="↺  Reset",
            command=self._do_reset).pack(side=tk.LEFT, padx=2, pady=6)

        sep()

        # Step counter
        self._step_label = ttk.Label(tb, text="Step  0",
            font=("Courier", 11, "bold"), background="#111220",
            foreground=ACCENT)
        self._step_label.pack(side=tk.LEFT, padx=10)

        # Help hint (right-aligned)
        hint = ttk.Label(tb,
            text="Right-click canvas to add node  ·  Right-click node for options  ·  Drag to reposition",
            background="#111220", foreground=TEXT_DIM, font=("TkDefaultFont", 8))
        hint.pack(side=tk.RIGHT, padx=12)

    # ──────────────────────────────────────────────────────────────────
    # Main area: canvas + right panel
    # ──────────────────────────────────────────────────────────────────

    def _build_main_area(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # ---- Canvas ----
        self.canvas = tk.Canvas(paned, bg=BG, highlightthickness=0,
                                cursor="crosshair")
        paned.add(self.canvas, weight=3)

        self.canvas.bind("<Button-1>",        self._on_canvas_click)
        self.canvas.bind("<B1-Motion>",       self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Button-3>",        self._on_canvas_right)
        self.canvas.bind("<Configure>",       self._on_canvas_resize)

        # ---- Right panel ----
        right = tk.Frame(paned, bg=PANEL_BG, width=280)
        paned.add(right, weight=1)
        right.pack_propagate(False)

        # Inspector header
        self._inspector_title = ttk.Label(right, text="No selection",
            font=FONT_BOLD)
        self._inspector_title.pack(fill=tk.X, padx=10, pady=(10, 2))

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8)

        # Node state display
        state_frame = tk.Frame(right, bg=PANEL_BG)
        state_frame.pack(fill=tk.X, padx=10, pady=6)
        self._state_text = tk.Text(state_frame, height=7, bg="#252840",
            fg=TEXT, font=FONT_MONO, relief="flat", state="disabled",
            wrap="word")
        self._state_text.pack(fill=tk.X)

        # Action buttons (shown when a node is selected)
        self._action_frame = tk.Frame(right, bg=PANEL_BG)
        self._action_frame.pack(fill=tk.X, padx=10, pady=2)

        ttk.Button(self._action_frame, text="✉  Inject event",
            command=self._inject_event_dialog).pack(
            side=tk.LEFT, padx=(0, 4))
        ttk.Button(self._action_frame, text="⌖  Draw edge",
            command=self._start_edge_draw).pack(side=tk.LEFT)
        ttk.Button(self._action_frame, text="✕  Remove",
            command=self._remove_selected).pack(side=tk.RIGHT)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=6)

        # Add-node panel
        add_header = ttk.Label(right, text="Add Node", font=FONT_BOLD)
        add_header.pack(fill=tk.X, padx=10)

        add_form = tk.Frame(right, bg=PANEL_BG)
        add_form.pack(fill=tk.X, padx=10, pady=4)

        ttk.Label(add_form, text="Type:").grid(row=0, column=0, sticky="w", pady=2)
        self._new_type = tk.StringVar(value="counter")
        tc = ttk.Combobox(add_form, textvariable=self._new_type,
            values=list(NODE_REGISTRY), width=10, state="readonly")
        tc.grid(row=0, column=1, sticky="ew", padx=(4,0))

        ttk.Label(add_form, text="Label:").grid(row=1, column=0, sticky="w", pady=2)
        self._new_label = tk.StringVar()
        ttk.Entry(add_form, textvariable=self._new_label, width=12).grid(
            row=1, column=1, sticky="ew", padx=(4,0))

        add_form.columnconfigure(1, weight=1)

        ttk.Button(right, text="＋  Add Node",
            command=self._add_node_from_panel,
            style="Accent.TButton").pack(fill=tk.X, padx=10, pady=4)

        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=4)

        # Event log
        ttk.Label(right, text="Event Log", font=FONT_BOLD).pack(
            fill=tk.X, padx=10)

        log_frame = tk.Frame(right, bg=PANEL_BG)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self._log_text = tk.Text(log_frame, bg="#0d0f1a", fg=ACCENT2,
            font=FONT_MONO, relief="flat", state="disabled", wrap="none")
        log_sb = ttk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text["yscrollcommand"] = log_sb.set
        log_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(fill=tk.BOTH, expand=True)

    # ──────────────────────────────────────────────────────────────────
    # Status bar
    # ──────────────────────────────────────────────────────────────────

    def _build_statusbar(self):
        sb = tk.Frame(self, bg="#0d0f1a", height=22)
        sb.pack(side=tk.BOTTOM, fill=tk.X)
        sb.pack_propagate(False)
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(sb, textvariable=self._status_var, bg="#0d0f1a",
            fg=TEXT_DIM, font=("TkDefaultFont", 8), anchor="w",
            padx=10).pack(fill=tk.X)

    def _set_status(self, msg: str):
        self._status_var.set(msg)

    # ──────────────────────────────────────────────────────────────────
    # Drawing
    # ──────────────────────────────────────────────────────────────────

    def _draw(self):
        c = self.canvas
        c.delete("all")
        snap = self.graph.snapshot()
        node_map = {n["id"]: n for n in snap["nodes"]}

        w = c.winfo_width() or 800
        h = c.winfo_height() or 600
        self.layout.width  = w
        self.layout.height = h
        self.layout.sync([n["id"] for n in snap["nodes"]])

        now = time.time()

        # Draw edges
        for e in snap["edges"]:
            src, dst = e["src"], e["dst"]
            if src not in self.layout.pos or dst not in self.layout.pos:
                continue
            x1, y1 = self.layout.pos[src]
            x2, y2 = self.layout.pos[dst]

            key = (src, dst)
            active = self._active_edges.get(key, 0) > now
            color  = EDGE_ACTIVE if active else EDGE_COLOR
            width  = 2.5 if active else 1.5

            # Shorten line to not overlap node circles
            dx, dy = x2 - x1, y2 - y1
            dist = math.hypot(dx, dy) or 1
            ux, uy = dx / dist, dy / dist
            ex1 = x1 + ux * (NODE_R + 3)
            ey1 = y1 + uy * (NODE_R + 3)
            ex2 = x2 - ux * (NODE_R + 8)
            ey2 = y2 - uy * (NODE_R + 8)

            c.create_line(ex1, ey1, ex2, ey2,
                fill=color, width=width,
                arrow=tk.LAST, arrowshape=(10, 13, 4),
                tags="edge")

        # Draw nodes
        for n in snap["nodes"]:
            nid = n["id"]
            if nid not in self.layout.pos:
                continue
            x, y = self.layout.pos[nid]
            color   = n.get("color", ACCENT)
            is_sel  = nid == self._selected
            is_edge_src = nid == self._edge_src

            # Glow ring for selected
            if is_sel:
                c.create_oval(x - NODE_R - 5, y - NODE_R - 5,
                              x + NODE_R + 5, y + NODE_R + 5,
                              fill="", outline=color, width=2.5,
                              tags=f"node_{nid}")
            if is_edge_src:
                c.create_oval(x - NODE_R - 5, y - NODE_R - 5,
                              x + NODE_R + 5, y + NODE_R + 5,
                              fill="", outline=ACCENT2, width=2.5, dash=(4, 3),
                              tags=f"node_{nid}")

            # Shadow
            c.create_oval(x - NODE_R + 3, y - NODE_R + 4,
                          x + NODE_R + 3, y + NODE_R + 4,
                          fill="#000000", outline="", stipple="gray25",
                          tags=f"node_{nid}")
            # Body
            c.create_oval(x - NODE_R, y - NODE_R, x + NODE_R, y + NODE_R,
                fill=color,
                outline="white" if is_sel else color,
                width=2,
                tags=f"node_{nid}")

            # Label
            label = n["label"]
            if len(label) > 9:
                label = label[:8] + "…"
            c.create_text(x, y, text=label, fill="white",
                font=("TkDefaultFont", 8, "bold"),
                tags=f"node_{nid}")

            # Small state badge (first key=value)
            state = n.get("state", {})
            if state:
                k, v = next(iter(state.items()))
                badge = f"{k}={v}"
                c.create_text(x, y + NODE_R + 10, text=badge,
                    fill=TEXT_DIM, font=("TkDefaultFont", 7),
                    tags=f"node_{nid}")

            # Bind events on node tags
            c.tag_bind(f"node_{nid}", "<Button-1>",
                lambda e, n=nid: self._on_node_click(e, n))
            c.tag_bind(f"node_{nid}", "<B1-Motion>",
                lambda e, n=nid: self._on_node_drag(e, n))
            c.tag_bind(f"node_{nid}", "<ButtonRelease-1>",
                lambda e, n=nid: self._on_node_release(e, n))
            c.tag_bind(f"node_{nid}", "<Button-3>",
                lambda e, n=nid: self._on_node_right(e, n))

        # Edge-draw mode hint
        if self._edge_src:
            c.create_text(10, 10,
                text=f"Click target node to connect edge from '{self._edge_src}' — Esc to cancel",
                fill=ACCENT2, font=FONT_UI, anchor="nw")

    def _schedule_redraw(self):
        self.after(0, self._redraw_and_refresh)

    def _redraw_and_refresh(self):
        self._draw()
        self._refresh_inspector()
        snap = self.graph.snapshot()
        self._step_label.config(text=f"Step  {snap['step']}")
        # Update event log
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        for line in snap["event_log"]:
            self._log_text.insert("end", line + "\n")
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    # ──────────────────────────────────────────────────────────────────
    # Force layout ticker
    # ──────────────────────────────────────────────────────────────────

    def _tick_layout(self):
        snap = self.graph.snapshot()
        self.layout.sync([n["id"] for n in snap["nodes"]])
        self.layout.tick(snap["edges"], pinned=self._pinned, iterations=2)
        self._draw()
        self._layout_job = self.after(40, self._tick_layout)   # ~25 fps

    # ──────────────────────────────────────────────────────────────────
    # Inspector panel
    # ──────────────────────────────────────────────────────────────────

    def _refresh_inspector(self):
        nid = self._selected
        snap = self.graph.snapshot()
        node_map = {n["id"]: n for n in snap["nodes"]}

        if nid and nid in node_map:
            n = node_map[nid]
            self._inspector_title.config(
                text=f"{n['label']}  [{n['id']}]  ({self.graph.nodes[nid].__class__.__name__})")
            self._state_text.config(state="normal")
            self._state_text.delete("1.0", "end")
            for k, v in n["state"].items():
                self._state_text.insert("end", f"{k}: {v}\n")
            self._state_text.insert("end", "\n— recent log —\n")
            for line in n["log"]:
                self._state_text.insert("end", line + "\n")
            self._state_text.config(state="disabled")
        else:
            self._inspector_title.config(text="No selection")
            self._state_text.config(state="normal")
            self._state_text.delete("1.0", "end")
            self._state_text.config(state="disabled")

    # ──────────────────────────────────────────────────────────────────
    # Canvas mouse events
    # ──────────────────────────────────────────────────────────────────

    def _node_at(self, x: float, y: float) -> Optional[str]:
        """Return node id under cursor, or None."""
        for nid, (nx, ny) in self.layout.pos.items():
            if math.hypot(x - nx, y - ny) <= NODE_R + 4:
                return nid
        return None

    def _on_canvas_click(self, event):
        nid = self._node_at(event.x, event.y)
        if nid is None:
            if self._edge_src:
                self._edge_src = None
                self._draw()
            else:
                self._selected = None
            return

        if self._edge_src and self._edge_src != nid:
            self.graph.add_edge(self._edge_src, nid)
            self._set_status(f"Edge added: {self._edge_src} → {nid}")
            self._edge_src = None
        else:
            self._selected = nid
        self._refresh_inspector()

    def _on_canvas_drag(self, event):
        nid = self._drag_node
        if nid:
            self.layout.pos[nid] = [event.x, event.y]
            self._pinned[nid] = (event.x, event.y)

    def _on_canvas_release(self, event):
        self._drag_node = None

    def _on_canvas_right(self, event):
        nid = self._node_at(event.x, event.y)
        if nid:
            self._on_node_right(event, nid)
            return
        menu = tk.Menu(self, tearoff=0, bg=PANEL_BG, fg=TEXT,
                       activebackground=ACCENT, activeforeground="white",
                       relief="flat", bd=1)
        for tname in NODE_REGISTRY:
            menu.add_command(label=f"Add {tname} node",
                command=lambda t=tname, x=event.x, y=event.y: self._add_node_at(t, x, y))
        menu.post(event.x_root, event.y_root)

    def _on_canvas_resize(self, event):
        self.layout.width  = event.width
        self.layout.height = event.height

    # ──────────────────────────────────────────────────────────────────
    # Node mouse events
    # ──────────────────────────────────────────────────────────────────

    def _on_node_click(self, event, nid):
        if self._edge_src and self._edge_src != nid:
            self.graph.add_edge(self._edge_src, nid)
            self._set_status(f"Edge added: {self._edge_src} → {nid}")
            self._edge_src = None
        else:
            self._selected = nid
            self._drag_node = nid
        self._refresh_inspector()

    def _on_node_drag(self, event, nid):
        self.layout.pos[nid] = [event.x, event.y]
        self._pinned[nid] = (event.x, event.y)

    def _on_node_release(self, event, nid):
        self._drag_node = None

    def _on_node_right(self, event, nid):
        node = self.graph.nodes.get(nid)
        if not node:
            return
        menu = tk.Menu(self, tearoff=0, bg=PANEL_BG, fg=TEXT,
                       activebackground=ACCENT, activeforeground="white",
                       relief="flat", bd=1)
        menu.add_command(label=f"[{node.label}]  {nid}", state="disabled")
        menu.add_separator()
        menu.add_command(label="✉  Inject event…",
            command=lambda: self._inject_event_dialog(nid))
        menu.add_command(label="⌖  Start edge from here",
            command=lambda: self._start_edge_draw(nid))
        menu.add_command(label="📌  Unpin",
            command=lambda: self._pinned.pop(nid, None))
        menu.add_separator()

        # Show existing edges
        nbs = self.graph.neighbours(nid)
        if nbs:
            for nb in nbs:
                nb_label = self.graph.nodes[nb].label if nb in self.graph.nodes else nb
                menu.add_command(
                    label=f"  ✕ Remove edge → {nb_label}",
                    command=lambda s=nid, d=nb: (
                        self.graph.remove_edge(s, d),
                        self._set_status(f"Edge removed: {s} → {d}")
                    ))
        menu.add_separator()
        menu.add_command(label="🗑  Remove node",
            command=lambda: self._remove_node(nid))
        menu.post(event.x_root, event.y_root)

    # ──────────────────────────────────────────────────────────────────
    # Toolbar actions
    # ──────────────────────────────────────────────────────────────────

    def _do_step(self, n: int = 1):
        self.graph.step(n)
        self._set_status(f"Stepped ×{n}  (total: {self.graph.step_count})")

    def _toggle_play(self):
        self._playing = not self._playing
        if self._playing:
            self._play_btn_var.set("⏸  Pause")
            self._schedule_auto_step()
        else:
            self._play_btn_var.set("⏵  Play")
            if self._play_job:
                self.after_cancel(self._play_job)
                self._play_job = None

    def _schedule_auto_step(self):
        if not self._playing:
            return
        self.graph.step()
        interval = STEP_INTERVALS.get(self._speed_var.get(), 300)
        self._play_job = self.after(interval, self._schedule_auto_step)

    def _do_reset(self):
        if not messagebox.askyesno("Reset", "Reset simulation step counter and all node states?"):
            return
        self._playing = False
        self._play_btn_var.set("⏵  Play")
        if self._play_job:
            self.after_cancel(self._play_job)
        self.graph.step_count = 0
        self.graph._event_log.clear()
        for node in self.graph.nodes.values():
            node._inbox.clear()
            node._log.clear()
            # Reset state for built-ins
            if isinstance(node, CounterNode):
                node.state["count"] = 0
            elif isinstance(node, RelayNode):
                node.state["forwarded"] = 0
            elif isinstance(node, SinkNode):
                node.state["received"] = 0
            elif isinstance(node, RandomWalkerNode):
                node.state["tokens_seen"] = 0
        self._set_status("Simulation reset.")
        self._schedule_redraw()

    # ──────────────────────────────────────────────────────────────────
    # Node / edge management
    # ──────────────────────────────────────────────────────────────────

    def _add_node_at(self, type_name: str, x: float, y: float):
        label = simpledialog.askstring("Label", f"Label for new {type_name} node:",
            parent=self, initialvalue=type_name.capitalize())
        if label is None:
            return
        node = self.graph.create_node(type_name, label=label)
        self.layout.add_node(node.id, x=x, y=y)
        self._pinned[node.id] = (x, y)
        self._selected = node.id
        self._set_status(f"Added {type_name} node '{label}' ({node.id})")

    def _add_node_from_panel(self):
        type_name = self._new_type.get()
        label     = self._new_label.get().strip() or type_name.capitalize()
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        x = random.uniform(w * 0.2, w * 0.8)
        y = random.uniform(h * 0.2, h * 0.8)
        node = self.graph.create_node(type_name, label=label)
        self.layout.add_node(node.id, x=x, y=y)
        self._selected = node.id
        self._new_label.set("")
        self._set_status(f"Added {type_name} node '{label}' ({node.id})")

    def _remove_node(self, nid: str):
        label = self.graph.nodes[nid].label if nid in self.graph.nodes else nid
        self.graph.remove_node(nid)
        self.layout.remove_node(nid)
        self._pinned.pop(nid, None)
        if self._selected == nid:
            self._selected = None
        self._set_status(f"Removed node '{label}' ({nid})")

    def _remove_selected(self):
        if self._selected:
            self._remove_node(self._selected)

    def _start_edge_draw(self, nid: str = None):
        src = nid or self._selected
        if not src:
            self._set_status("Select a source node first.")
            return
        self._edge_src = src
        self._set_status(f"Edge mode: click target node to connect from '{src}' (right-click to cancel)")

    def _inject_event_dialog(self, nid: str = None):
        target = nid or self._selected
        if not target:
            self._set_status("Select a node first.")
            return
        ev_type = simpledialog.askstring("Inject Event",
            f"Event type to inject into '{target}':",
            parent=self, initialvalue="ping")
        if ev_type:
            self.graph.inject_event(target, ev_type)
            self._set_status(f"Injected '{ev_type}' into {target}")
            self._schedule_redraw()

    # ──────────────────────────────────────────────────────────────────
    # Keyboard shortcuts
    # ──────────────────────────────────────────────────────────────────

    def _bind_keys(self):
        self.bind("<space>",  lambda e: self._do_step())
        self.bind("<Return>", lambda e: self._toggle_play())
        self.bind("<Escape>", lambda e: self._cancel_edge_draw())
        self.bind("<Delete>", lambda e: self._remove_selected())
        self.bind("<BackSpace>", lambda e: self._remove_selected())

    def _cancel_edge_draw(self):
        if self._edge_src:
            self._edge_src = None
            self._set_status("Edge draw cancelled.")
            self._draw()


# ──────────────────────────────────────────────────────────────────────────────
# Seed graph & launch
# ──────────────────────────────────────────────────────────────────────────────

def build_demo_graph() -> Graph:
    g = Graph()
    a = CounterNode(node_id="A", label="Counter A", broadcast_every=2)
    b = RelayNode  (node_id="B", label="Relay B")
    c = SinkNode   (node_id="C", label="Sink C")
    d = RandomWalkerNode(node_id="D", label="Walker D")
    for n in (a, b, c, d):
        g.add_node(n)
    g.add_edge("A", "B")
    g.add_edge("B", "C")
    g.add_edge("A", "C")
    g.add_edge("D", "B")
    g.add_edge("C", "D")
    return g


if __name__ == "__main__":
    graph = build_demo_graph()
    app = App(graph)
    app._bind_keys()
    app.mainloop()