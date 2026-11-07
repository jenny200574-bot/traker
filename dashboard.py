"""
dashboard.py — Widgets del Dashboard: tabla de productos y ventana de detalle con gráfica.
"""
from __future__ import annotations

import io
import threading
from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageTk

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    _HAS_MATPLOTLIB = True
except ImportError:
    _HAS_MATPLOTLIB = False

import database as db

# ─────────────────────────── CONSTANTES ─────────────────────────────────────

THUMB_SIZE = (56, 56)
PLACEHOLDER_COLOR = "#2a2d3e"
ROW_HEIGHT = 70
ROW_BG_EVEN  = "#1e2130"
ROW_BG_ODD   = "#171929"
ROW_HOVER    = "#252840"
ACCENT_COLOR = "#4f8ef7"

# ─────────────────────────── CACHE DE IMÁGENES ──────────────────────────────

_image_cache: dict[str, ctk.CTkImage] = {}
_image_lock = threading.Lock()


def _load_image_async(url: str, callback: Callable[[ctk.CTkImage], None]) -> None:
    """Descarga una imagen en background y llama callback en cualquier caso."""
    def _worker():
        with _image_lock:
            if url in _image_cache:
                callback(_image_cache[url])
                return

        if not url or not _HAS_REQUESTS:
            return

        try:
            resp = _requests.get(url, timeout=10)
            resp.raise_for_status()
            pil_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            pil_img.thumbnail(THUMB_SIZE, Image.LANCZOS)

            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=THUMB_SIZE)
            with _image_lock:
                _image_cache[url] = ctk_img
            callback(ctk_img)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _make_placeholder() -> ctk.CTkImage:
    img = Image.new("RGBA", THUMB_SIZE, color=(42, 45, 62, 255))
    return ctk.CTkImage(light_image=img, dark_image=img, size=THUMB_SIZE)


# ─────────────────────────── FILA DE PRODUCTO ───────────────────────────────

class ProductRow(ctk.CTkFrame):
    """Una fila en la tabla del dashboard."""

    def __init__(
        self,
        master,
        producto: dict,
        stock_anterior: Optional[int],
        on_click: Callable[[dict], None],
        row_index: int,
    ):
        bg = ROW_BG_EVEN if row_index % 2 == 0 else ROW_BG_ODD
        super().__init__(master, fg_color=bg, corner_radius=0, height=ROW_HEIGHT)
        self.pack(fill="x", padx=0, pady=0)
        self.pack_propagate(False)

        self.producto = producto
        self.on_click = on_click
        self._bg = bg

        self.columnconfigure(0, minsize=72)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, minsize=110)
        self.columnconfigure(3, minsize=110)
        self.columnconfigure(4, minsize=140)

        # ── Imagen ──────────────────────────────────────────────────────────
        self._placeholder = _make_placeholder()
        self.img_label = ctk.CTkLabel(self, image=self._placeholder, text="")
        self.img_label.grid(row=0, column=0, padx=(12, 6), pady=8, sticky="ns")

        imagen_url = producto.get("imagen", "")
        if imagen_url:
            _load_image_async(imagen_url, self._set_image)

        # ── Nombre + SKU ─────────────────────────────────────────────────────
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=1, padx=6, pady=8, sticky="nsw")

        ctk.CTkLabel(
            info_frame,
            text=producto.get("nombre", "—")[:55],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#e8eaf6",
            anchor="w",
        ).pack(anchor="w")

        sku = producto.get("sku", "")
        if sku:
            ctk.CTkLabel(
                info_frame,
                text=f"SKU: {sku}",
                font=ctk.CTkFont(size=11),
                text_color="#7986cb",
                anchor="w",
            ).pack(anchor="w")

        # ── Stock actual ─────────────────────────────────────────────────────
        stock = producto.get("stock", 0)
        ctk.CTkLabel(
            self,
            text=f"{stock:,}",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#e8eaf6",
        ).grid(row=0, column=2, padx=8, pady=8)

        # ── Stock anterior ───────────────────────────────────────────────────
        if stock_anterior is not None:
            txt_ant = f"{stock_anterior:,}"
        else:
            txt_ant = "—"

        ctk.CTkLabel(
            self,
            text=txt_ant,
            font=ctk.CTkFont(size=14),
            text_color="#b0bec5",
        ).grid(row=0, column=3, padx=8, pady=8)

        # ── Ventas (Stock anterior - Stock actual) ───────────────────────────
        ventas_frame = ctk.CTkFrame(self, fg_color="transparent")
        ventas_frame.grid(row=0, column=4, padx=(4, 12), pady=8)

        if stock_anterior is not None:
            if stock_anterior > stock:
                # Disminución → Ventas
                ventas_val = stock_anterior - stock
                lbl_txt = f"📉 −{ventas_val:,}"
                lbl_col = "#ef5350"
            elif stock_anterior < stock:
                # Aumento → Reposición
                rep_val = stock - stock_anterior
                lbl_txt = f"🟢 0 (+{rep_val:,} Rep)"
                lbl_col = "#66bb6a"
            else:
                # Sin cambios
                lbl_txt = "⚪ 0"
                lbl_col = "#90a4ae"
        else:
            lbl_txt = "⚪ —"
            lbl_col = "#546e7a"

        ctk.CTkLabel(
            ventas_frame,
            text=lbl_txt,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=lbl_col,
        ).pack()

        # ── Hover + Click ────────────────────────────────────────────────────
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        for widget in self.winfo_children():
            widget.bind("<Button-1>", self._on_click)
            for sub in widget.winfo_children():
                sub.bind("<Button-1>", self._on_click)

    def _set_image(self, img: ctk.CTkImage) -> None:
        try:
            self.img_label.configure(image=img)
        except Exception:
            pass

    def _on_enter(self, _event=None) -> None:
        self.configure(fg_color=ROW_HOVER)

    def _on_leave(self, _event=None) -> None:
        self.configure(fg_color=self._bg)

    def _on_click(self, _event=None) -> None:
        self.on_click(self.producto)


# ─────────────────────────── TABLA DEL DASHBOARD ────────────────────────────

class DashboardTable(ctk.CTkScrollableFrame):
    """Tabla desplazable de productos con indicadores de stock."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="#13152a", **kwargs)
        self._header()
        self._rows: list[ProductRow] = []
        self._on_click_cb: Optional[Callable[[dict], None]] = None
        self._all_productos: list[dict] = []
        self._stock_anterior: dict = {}
        self._search_query: str = ""
        self._filter_type: str = "todos"
        self._visible_limit: int = 100
        self._load_more_frame: Optional[ctk.CTkFrame] = None

    def _header(self) -> None:
        h = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=0, height=38)
        h.pack(fill="x", padx=0, pady=(0, 2))
        h.pack_propagate(False)
        h.columnconfigure(0, minsize=72)
        h.columnconfigure(1, weight=1)
        h.columnconfigure(2, minsize=110)
        h.columnconfigure(3, minsize=110)
        h.columnconfigure(4, minsize=140)

        font = ctk.CTkFont(size=12, weight="bold")
        color = "#546e7a"

        for col, text in enumerate(["Imagen", "Producto", "Stock actual", "Stock anterior", "Ventas"]):
            ctk.CTkLabel(h, text=text, font=font, text_color=color).grid(
                row=0, column=col, padx=8, pady=8, sticky="w"
            )

    def set_on_click(self, cb: Callable[[dict], None]) -> None:
        self._on_click_cb = cb

    def set_search_query(self, query: str) -> None:
        self._search_query = query.strip().lower()
        self._visible_limit = 100
        self._render()

    def set_filter_type(self, filter_type: str) -> None:
        self._filter_type = filter_type.strip().lower()
        self._visible_limit = 100
        self._render()

    def refresh(self, productos: list[dict], stock_anterior: dict) -> None:
        self._all_productos = productos
        self._stock_anterior = stock_anterior
        self._visible_limit = 100
        self._render()

    def _render(self) -> None:
        # Destruir filas anteriores
        for row in self._rows:
            try:
                row.destroy()
            except Exception:
                pass
        self._rows.clear()

        # Destruir botón "Ver más" si existe
        if self._load_more_frame:
            try:
                self._load_more_frame.destroy()
            except Exception:
                pass
            self._load_more_frame = None

        # Filtrar productos
        filtered = self._all_productos

        # 1. Filtro por búsqueda
        q = self._search_query
        if q:
            filtered = [
                p for p in filtered
                if q in (p.get("nombre") or "").lower()
                or q in (p.get("sku") or "").lower()
            ]

        # 2. Filtro por tipo de variación
        if self._filter_type == "bajaron":
            filtered = [
                p for p in filtered
                if self._stock_anterior.get(p["product_id_dropi"]) is not None
                and self._stock_anterior[p["product_id_dropi"]] > p["stock"]
            ]
        elif self._filter_type == "subieron":
            filtered = [
                p for p in filtered
                if self._stock_anterior.get(p["product_id_dropi"]) is not None
                and self._stock_anterior[p["product_id_dropi"]] < p["stock"]
            ]

        # Slice de los productos a mostrar
        to_show = filtered[:self._visible_limit]
        for i, p in enumerate(to_show):
            ant = self._stock_anterior.get(p["product_id_dropi"])
            row = ProductRow(self, p, ant, self._click_handler, i)
            self._rows.append(row)

        # Si hay más productos, crear botón "Ver más"
        if len(filtered) > self._visible_limit:
            self._load_more_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._load_more_frame.pack(fill="x", pady=10)
            
            btn = ctk.CTkButton(
                self._load_more_frame,
                text=f"Mostrar más productos (mostrando {self._visible_limit} de {len(filtered)})",
                font=ctk.CTkFont(size=13, weight="bold"),
                fg_color="#2e3250",
                hover_color="#3a3f68",
                height=38,
                command=self._load_more
            )
            btn.pack(padx=20, pady=5, fill="x")

    def _load_more(self) -> None:
        self._visible_limit += 100
        self._render()

    def _click_handler(self, producto: dict) -> None:
        if self._on_click_cb:
            self._on_click_cb(producto)


# ─────────────────────────── VENTANA DETALLE ────────────────────────────────

class ProductDetailWindow(ctk.CTkToplevel):
    """Ventana emergente con historial y gráfica de evolución de stock."""

    def __init__(self, master, producto: dict, modo_ventas: bool = False):
        super().__init__(master)
        self.title(f"Detalle — {producto.get('nombre', '')[:60]}")
        self.geometry("820x640")
        self.resizable(True, True)
        self.grab_set()

        self._modo_ventas = modo_ventas

        pid  = producto["product_id_dropi"]
        sid  = producto["supplier_id"]
        rows = db.get_historial_producto(pid, sid)

        self._build_ui(producto, rows)

    def _build_ui(self, producto: dict, historial: list[dict]) -> None:
        # Cabecera
        header = ctk.CTkFrame(self, fg_color="#1b2036", corner_radius=10)
        header.pack(fill="x", padx=16, pady=(16, 8))

        # Imagen grande
        img_lbl = ctk.CTkLabel(header, text="📦", font=ctk.CTkFont(size=48))
        img_lbl.pack(side="left", padx=20, pady=16)

        imagen_url = producto.get("imagen", "")
        if imagen_url and _HAS_REQUESTS:
            def _set_big(img: ctk.CTkImage):
                try:
                    img_lbl.configure(image=img, text="")
                except Exception:
                    pass
            _load_image_async_large(imagen_url, _set_big)

        info = ctk.CTkFrame(header, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=8, pady=12)

        ctk.CTkLabel(
            info,
            text=producto.get("nombre", "—"),
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#e8eaf6",
            wraplength=480,
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        meta = f"SKU: {producto.get('sku', '—')}  |  Stock actual: {producto.get('stock', 0):,}  |  ID: {producto.get('product_id_dropi')}"
        ctk.CTkLabel(info, text=meta, text_color="#7986cb", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(4, 0))

        # Separador con pestañas internas
        tabs = ctk.CTkTabview(self, fg_color="#13152a")
        tabs.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        tab_graf = tabs.add("📈 Gráfica")
        tab_hist = tabs.add("📋 Historial")

        # Gráfica
        self._build_graph(tab_graf, historial, self._modo_ventas)

        # Tabla historial
        self._build_historial_table(tab_hist, historial)

    def _build_graph(self, parent, historial: list[dict], modo_ventas: bool = False) -> None:
        if not _HAS_MATPLOTLIB or not historial:
            ctk.CTkLabel(
                parent,
                text="Sin datos de historial para graficar.",
                text_color="#546e7a",
            ).pack(expand=True)
            return

        from datetime import datetime

        fechas = [datetime.fromisoformat(r["fecha_hora"]) for r in historial]
        stocks = [r["stock"] for r in historial]

        fig, ax = plt.subplots(figsize=(8, 3.8), facecolor="#13152a")
        ax.set_facecolor("#1b2036")

        if modo_ventas and len(stocks) > 1:
            ventas = [0] + [max(0, stocks[i - 1] - stocks[i]) for i in range(1, len(stocks))]

            ax.fill_between(fechas, ventas, alpha=0.18, color="#4f8ef7")
            ax.plot(fechas, ventas, color="#4f8ef7", linewidth=2.2, marker="o", markersize=5)

            for f, v in zip(fechas, ventas):
                if v > 0:
                    ax.annotate(
                        str(v),
                        (f, v),
                        textcoords="offset points",
                        xytext=(0, 10),
                        ha="center",
                        fontsize=9,
                        color="#4fc3f7",
                        fontweight="bold",
                    )

            ax.set_title("Ventas por período", color="#e8eaf6", fontsize=13, pad=10)
            ax.set_ylabel("Unidades vendidas", color="#7986cb")
        else:
            ax.fill_between(fechas, stocks, alpha=0.18, color="#4f8ef7")
            ax.plot(fechas, stocks, color="#4f8ef7", linewidth=2.2, marker="o", markersize=4)

            # Agregar etiquetas numéricas en cada punto
            for i, (fecha, stock) in enumerate(zip(fechas, stocks)):
                ax.annotate(
                    str(stock),
                    (fecha, stock),
                    textcoords="offset points",
                    xytext=(0, 10),
                    ha='center',
                    fontsize=8,
                    color='#4fc3f7',
                    fontweight='bold'
                )

            ax.set_title("Evolución del Stock", color="#e8eaf6", fontsize=13, pad=10)
            ax.set_ylabel("Stock", color="#7986cb")

        ax.tick_params(colors="#7986cb")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
        fig.autofmt_xdate(rotation=35)

        for spine in ax.spines.values():
            spine.set_edgecolor("#2a2d3e")
        ax.yaxis.set_tick_params(labelcolor="#7986cb")
        ax.xaxis.set_tick_params(labelcolor="#7986cb")

        fig.tight_layout(pad=1.5)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_historial_table(self, parent, historial: list[dict]) -> None:
        import tkinter.ttk as ttk

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Detail.Treeview",
            background="#1b2036",
            foreground="#e8eaf6",
            fieldbackground="#1b2036",
            rowheight=26,
            font=("Segoe UI", 11),
        )
        style.configure(
            "Detail.Treeview.Heading",
            background="#0d1117",
            foreground="#7986cb",
            font=("Segoe UI", 11, "bold"),
        )
        style.map("Detail.Treeview", background=[("selected", "#252840")])

        frame = ctk.CTkFrame(parent, fg_color="#13152a")
        frame.pack(fill="both", expand=True, padx=4, pady=4)

        tree = ttk.Treeview(
            frame,
            columns=("fecha", "stock"),
            show="headings",
            style="Detail.Treeview",
        )
        tree.heading("fecha", text="Fecha y Hora")
        tree.heading("stock", text="Stock")
        tree.column("fecha", width=200)
        tree.column("stock", width=100, anchor="center")

        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)

        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for row in reversed(historial):
            tree.insert("", "end", values=(row["fecha_hora"], row["stock"]))


def _load_image_async_large(
    url: str, callback: Callable[[ctk.CTkImage], None]
) -> None:
    """Like _load_image_async but for larger images (120x120)."""
    BIG = (120, 120)

    def _worker():
        try:
            resp = _requests.get(url, timeout=10)
            resp.raise_for_status()
            pil_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            pil_img.thumbnail(BIG, Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=BIG)
            callback(ctk_img)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


class HotProductRow(ctk.CTkFrame):
    """Una fila en la tabla de productos más vendidos (Más Vendidos)."""

    def __init__(
        self,
        master,
        rank: int,
        producto: dict,
        on_click: Callable[[dict], None],
        row_index: int,
    ):
        bg = ROW_BG_EVEN if row_index % 2 == 0 else ROW_BG_ODD
        super().__init__(master, fg_color=bg, corner_radius=0, height=ROW_HEIGHT)
        self.pack(fill="x", padx=0, pady=0)
        self.pack_propagate(False)

        self.producto = producto
        self.on_click = on_click
        self._bg = bg

        self.columnconfigure(0, minsize=40)
        self.columnconfigure(1, minsize=72)
        self.columnconfigure(2, weight=1)
        self.columnconfigure(3, minsize=140)
        self.columnconfigure(4, minsize=100)
        self.columnconfigure(5, minsize=100)
        self.columnconfigure(6, minsize=140)

        # ── Puesto (Rank) ───────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text=f"#{rank}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffb74d",
        ).grid(row=0, column=0, padx=6, pady=8)

        # ── Imagen ──────────────────────────────────────────────────────────
        self._placeholder = _make_placeholder()
        self.img_label = ctk.CTkLabel(self, image=self._placeholder, text="")
        self.img_label.grid(row=0, column=1, padx=(6, 6), pady=8, sticky="ns")

        imagen_url = producto.get("imagen", "")
        if imagen_url:
            _load_image_async(imagen_url, self._set_image)

        # ── Nombre + SKU ─────────────────────────────────────────────────────
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=2, padx=6, pady=8, sticky="nsw")

        ctk.CTkLabel(
            info_frame,
            text=producto.get("nombre", "—")[:55],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#e8eaf6",
            anchor="w",
        ).pack(anchor="w")

        sku = producto.get("sku", "")
        if sku:
            ctk.CTkLabel(
                info_frame,
                text=f"SKU: {sku}",
                font=ctk.CTkFont(size=11),
                text_color="#7986cb",
                anchor="w",
            ).pack(anchor="w")

        # ── Bodega ───────────────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text=producto.get("bodega_nombre", "—")[:18],
            font=ctk.CTkFont(size=13),
            text_color="#b0bec5",
            anchor="w",
        ).grid(row=0, column=3, padx=8, pady=8, sticky="w")

        # ── Stock actual ─────────────────────────────────────────────────────
        stock = producto.get("stock_actual", 0)
        ctk.CTkLabel(
            self,
            text=f"{stock:,}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#e8eaf6",
        ).grid(row=0, column=4, padx=8, pady=8)

        # ── Stock anterior ───────────────────────────────────────────────────
        stock_ant = producto.get("stock_anterior")
        if stock_ant is not None:
            txt_ant = f"{stock_ant:,}"
        else:
            txt_ant = "—"

        ctk.CTkLabel(
            self,
            text=txt_ant,
            font=ctk.CTkFont(size=13),
            text_color="#90a4ae",
        ).grid(row=0, column=5, padx=8, pady=8)

        # ── Ventas estimadas ─────────────────────────────────────────────────
        ventas = producto.get("ventas_estimadas", 0)
        ctk.CTkLabel(
            self,
            text=f"📉 −{ventas:,}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#ef5350",
        ).grid(row=0, column=6, padx=(8, 12), pady=8)

        # ── Hover + Click ────────────────────────────────────────────────────
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        for widget in self.winfo_children():
            widget.bind("<Button-1>", self._on_click)
            for sub in widget.winfo_children():
                sub.bind("<Button-1>", self._on_click)

    def _set_image(self, img: ctk.CTkImage) -> None:
        try:
            self.img_label.configure(image=img)
        except Exception:
            pass

    def _on_enter(self, _event=None) -> None:
        self.configure(fg_color=ROW_HOVER)

    def _on_leave(self, _event=None) -> None:
        self.configure(fg_color=self._bg)

    def _on_click(self, _event=None) -> None:
        prod_map = {
            "product_id_dropi": self.producto["product_id_dropi"],
            "supplier_id": self.producto["supplier_id"],
            "nombre": self.producto["nombre"],
            "sku": self.producto["sku"],
            "imagen": self.producto["imagen"],
            "stock": self.producto["stock_actual"],
        }
        self.on_click(prod_map)


class HotDashboardTable(ctk.CTkScrollableFrame):
    """Tabla desplazable para los productos más vendidos."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="#13152a", **kwargs)
        self._header()
        self._rows: list[HotProductRow] = []
        self._on_click_cb: Optional[Callable[[dict], None]] = None
        self._all_productos: list[dict] = []

    def _header(self) -> None:
        h = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=0, height=38)
        h.pack(fill="x", padx=0, pady=(0, 2))
        h.pack_propagate(False)
        h.columnconfigure(0, minsize=40)
        h.columnconfigure(1, minsize=72)
        h.columnconfigure(2, weight=1)
        h.columnconfigure(3, minsize=140)
        h.columnconfigure(4, minsize=100)
        h.columnconfigure(5, minsize=100)
        h.columnconfigure(6, minsize=140)

        font = ctk.CTkFont(size=12, weight="bold")
        color = "#546e7a"

        headers = ["#", "Imagen", "Producto", "Bodega", "Stock actual", "Stock anterior", "Ventas estimadas"]
        for col, text in enumerate(headers):
            ctk.CTkLabel(h, text=text, font=font, text_color=color).grid(
                row=0, column=col, padx=8, pady=8, sticky="w" if col in (1, 2, 3) else ""
            )

    def set_on_click(self, cb: Callable[[dict], None]) -> None:
        self._on_click_cb = cb

    def refresh(self, productos: list[dict]) -> None:
        self._all_productos = productos
        self._render()

    def _render(self) -> None:
        for row in self._rows:
            try:
                row.destroy()
            except Exception:
                pass
        self._rows.clear()

        for i, p in enumerate(self._all_productos):
            row = HotProductRow(self, i + 1, p, self._click_handler, i)
            self._rows.append(row)

    def _click_handler(self, producto: dict) -> None:
        if self._on_click_cb:
            self._on_click_cb(producto)


class VendidosDashboardTable(ctk.CTkScrollableFrame):
    """Tabla desplazable para productos con movimientos de stock (Todo lo vendido)."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="#13152a", **kwargs)
        self._header()
        self._rows: list[VendidosProductRow] = []
        self._on_click_cb: Optional[Callable[[dict], None]] = None
        self._all_productos: list[dict] = []

    def _header(self) -> None:
        h = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=0, height=38)
        h.pack(fill="x", padx=0, pady=(0, 2))
        h.pack_propagate(False)
        h.columnconfigure(0, weight=1)
        h.columnconfigure(1, minsize=140)
        h.columnconfigure(2, minsize=100)
        h.columnconfigure(3, minsize=100)
        h.columnconfigure(4, minsize=80)
        h.columnconfigure(5, minsize=80)

        font = ctk.CTkFont(size=12, weight="bold")
        color = "#546e7a"

        headers = ["Producto", "Bodega", "Stock actual", "Stock anterior", "Cambio", "Cambios"]
        for col, text in enumerate(headers):
            ctk.CTkLabel(h, text=text, font=font, text_color=color).grid(
                row=0, column=col, padx=8, pady=8, sticky="w" if col in (0, 1) else ""
            )

    def set_on_click(self, cb: Callable[[dict], None]) -> None:
        self._on_click_cb = cb

    def refresh(self, productos: list[dict]) -> None:
        self._all_productos = productos
        self._render()

    def _render(self) -> None:
        for row in self._rows:
            try:
                row.destroy()
            except Exception:
                pass
        self._rows.clear()

        for i, p in enumerate(self._all_productos):
            row = VendidosProductRow(self, p, self._click_handler, i)
            self._rows.append(row)

    def _click_handler(self, producto: dict) -> None:
        if self._on_click_cb:
            self._on_click_cb(producto)


class VendidosProductRow(ctk.CTkFrame):
    """Una fila en la tabla de productos con movimientos (Todo lo vendido)."""

    def __init__(
        self,
        master,
        producto: dict,
        on_click: Callable[[dict], None],
        row_index: int,
    ):
        bg = ROW_BG_EVEN if row_index % 2 == 0 else ROW_BG_ODD
        super().__init__(master, fg_color=bg, corner_radius=0, height=ROW_HEIGHT)
        self.pack(fill="x", padx=0, pady=0)
        self.pack_propagate(False)

        self.producto = producto
        self.on_click = on_click
        self._bg = bg

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, minsize=140)
        self.columnconfigure(2, minsize=100)
        self.columnconfigure(3, minsize=100)
        self.columnconfigure(4, minsize=80)
        self.columnconfigure(5, minsize=80)

        # ── Nombre + SKU ─────────────────────────────────────────────────────
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.grid(row=0, column=0, padx=6, pady=8, sticky="nsw")

        ctk.CTkLabel(
            info_frame,
            text=producto.get("nombre", "—")[:55],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#e8eaf6",
            anchor="w",
        ).pack(anchor="w")

        sku = producto.get("sku", "")
        if sku:
            ctk.CTkLabel(
                info_frame,
                text=f"SKU: {sku}",
                font=ctk.CTkFont(size=11),
                text_color="#7986cb",
                anchor="w",
            ).pack(anchor="w")

        # ── Bodega ───────────────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text=producto.get("bodega_nombre", "—")[:18],
            font=ctk.CTkFont(size=13),
            text_color="#b0bec5",
            anchor="w",
        ).grid(row=0, column=2, padx=8, pady=8, sticky="w")

        # ── Stock actual ─────────────────────────────────────────────────────
        stock = producto.get("stock_actual", 0)
        ctk.CTkLabel(
            self,
            text=str(stock),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#4fc3f7",
        ).grid(row=0, column=3, padx=8, pady=8)

        # ── Stock anterior ─────────────────────────────────────────────────────
        stock_anterior = producto.get("stock_anterior", 0)
        ctk.CTkLabel(
            self,
            text=str(stock_anterior),
            font=ctk.CTkFont(size=13),
            text_color="#b0bec5",
        ).grid(row=0, column=4, padx=8, pady=8)

        # ── Cambio ─────────────────────────────────────────────────────────────
        cambio = producto.get("cambio", 0)
        cambio_text = f"+{cambio}" if cambio > 0 else str(cambio)
        cambio_color = "#4caf50" if cambio > 0 else "#f44336" if cambio < 0 else "#9e9e9e"
        
        ctk.CTkLabel(
            self,
            text=cambio_text,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=cambio_color,
        ).grid(row=0, column=5, padx=8, pady=8)

        # ── Cantidad de cambios ─────────────────────────────────────────────────
        cambios = producto.get("cantidad_cambios", 0)
        ctk.CTkLabel(
            self,
            text=str(cambios),
            font=ctk.CTkFont(size=13),
            text_color="#ffb74d",
        ).grid(row=0, column=6, padx=8, pady=8)

        # ── Click handler ─────────────────────────────────────────────────────
        self.bind("<Button-1>", lambda e: self.on_click(producto))
        for child in self.winfo_children():
            child.bind("<Button-1>", lambda e: self.on_click(producto))

    def _set_image(self, img: ctk.CTkImage) -> None:
        try:
            self.img_label.configure(image=img)
        except Exception:
            pass
