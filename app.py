"""
app.py — Aplicación principal de Dropi Tracker Desktop.

Tecnologías:
    UI:          CustomTkinter
    DB:          SQLite (via database.py)
    API:         api_dropi.py
    Scheduler:   APScheduler (via scheduler.py)
    Export:      pandas + openpyxl (via export_excel.py)
    Gráficas:    matplotlib (via dashboard.py)
"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter.ttk as ttk
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import customtkinter as ctk
from tkinter import messagebox, filedialog

# ── Módulos del proyecto ──────────────────────────────────────────────────────
import database as db
import export_excel as exporter
import settings as cfg
from api_dropi import descargar_bodega, extraer_supplier_id, login_dropi
from dashboard import DashboardTable, HotDashboardTable, ProductDetailWindow
from models import Bodega, LogActividad
from scheduler import DropiScheduler
from paths import APP_DIR

# ── Apariencia global ─────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Paleta de colores
C_BG       = "#0f1123"
C_SURFACE  = "#1b2036"
C_SURFACE2 = "#242840"
C_ACCENT   = "#4f8ef7"
C_SUCCESS  = "#4caf50"
C_WARNING  = "#ff9800"
C_ERROR    = "#ef5350"
C_TEXT     = "#e8eaf6"
C_MUTED    = "#546e7a"


# ═════════════════════════════════════════════════════════════════════════════
#  VENTANA AGREGAR / EDITAR BODEGA
# ═════════════════════════════════════════════════════════════════════════════

class BodegaDialog(ctk.CTkToplevel):
    def __init__(self, master, bodega: Optional[Bodega] = None):
        super().__init__(master)
        self.title("Editar bodega" if bodega else "Agregar bodega")
        self.geometry("480x480")
        self.minsize(480, 480)
        self.resizable(True, False)
        self.grab_set()
        self.result: Optional[Bodega] = None

        self._build(bodega)

        # ── Enter guarda, Escape cancela ────────────────────────────────────
        self.bind("<Return>", lambda _e: self._guardar())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self, bodega: Optional[Bodega]) -> None:
        frame = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=12)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="📦 Datos de la bodega",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=C_TEXT,
        ).pack(anchor="w", padx=20, pady=(16, 8))

        # Nombre
        ctk.CTkLabel(frame, text="Nombre:", text_color=C_MUTED).pack(anchor="w", padx=20)
        self.e_nombre = ctk.CTkEntry(frame, width=400, placeholder_text="Ej: Xpacify")
        self.e_nombre.pack(padx=20, pady=(2, 10))
        self.e_nombre.focus_set()

        # URL
        ctk.CTkLabel(frame, text="URL de la bodega:", text_color=C_MUTED).pack(anchor="w", padx=20)
        self.e_url = ctk.CTkEntry(
            frame, width=400,
            placeholder_text="https://app.dropi.co/provider/12345"
        )
        self.e_url.pack(padx=20, pady=(2, 4))

        ctk.CTkLabel(
            frame,
            text="ℹ️  El supplier_id se extrae automáticamente de la URL.",
            text_color=C_MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(0, 4))

        # supplier_id manual (fallback)
        ctk.CTkLabel(frame, text="Supplier ID (manual si la URL no lo incluye):", text_color=C_MUTED).pack(anchor="w", padx=20)
        self.e_sid = ctk.CTkEntry(frame, width=200, placeholder_text="Ej: 12345")
        self.e_sid.pack(anchor="w", padx=20, pady=(2, 10))

        # productos_esperados
        ctk.CTkLabel(frame, text="Productos esperados en Dropi (opcional):", text_color=C_MUTED).pack(anchor="w", padx=20)
        self.e_esperados = ctk.CTkEntry(frame, width=200, placeholder_text="Ej: 902")
        self.e_esperados.pack(anchor="w", padx=20, pady=(2, 14))

        # Prellenar
        if bodega:
            self.e_nombre.insert(0, bodega.nombre)
            self.e_url.insert(0, bodega.url)
            self.e_sid.insert(0, str(bodega.supplier_id))
            self.e_esperados.insert(0, str(bodega.productos_esperados) if bodega.productos_esperados > 0 else "")

        # Separador visual
        ctk.CTkFrame(frame, fg_color="#2a2d3e", height=1).pack(fill="x", padx=20, pady=(4, 14))

        # Botones — siempre visibles al fondo
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="✅  Guardar",
            fg_color=C_ACCENT,
            hover_color="#3a7ae0",
            command=self._guardar,
            width=180,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_frame,
            text="Cancelar",
            fg_color=C_SURFACE2,
            hover_color="#2e3250",
            command=self.destroy,
            width=110,
            height=40,
        ).pack(side="left")

        ctk.CTkLabel(
            frame,
            text="💡 También puedes presionar Enter para guardar",
            text_color=C_MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(4, 0))

    def _guardar(self) -> None:
        nombre = self.e_nombre.get().strip()
        url    = self.e_url.get().strip()
        sid_txt = self.e_sid.get().strip()
        esperados_txt = self.e_esperados.get().strip()

        if not nombre:
            messagebox.showerror("Error", "El nombre es obligatorio.", parent=self)
            return

        # Intentar extraer supplier_id de la URL
        supplier_id = extraer_supplier_id(url) if url else None

        # Fallback: manual
        if not supplier_id and sid_txt:
            try:
                supplier_id = int(sid_txt)
            except ValueError:
                pass

        if not supplier_id or supplier_id <= 0:
            messagebox.showerror(
                "Error",
                "No se pudo extraer el supplier_id.\n"
                "Verifica la URL o ingrésalo manualmente.",
                parent=self,
            )
            return

        productos_esperados = 0
        if esperados_txt:
            try:
                productos_esperados = int(esperados_txt)
            except ValueError:
                messagebox.showerror("Error", "El valor de productos esperados debe ser numérico.", parent=self)
                return

        self.result = Bodega(nombre=nombre, url=url, supplier_id=supplier_id, productos_esperados=productos_esperados)
        self.destroy()


# ═════════════════════════════════════════════════════════════════════════════
#  APLICACIÓN PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════

class DropiTrackerApp(ctk.CTk):
    APP_NAME    = "Dropi Tracker"
    APP_VERSION = "1.1.0"
    WIN_SIZE    = "1160x750"
    WIN_MIN     = (900, 600)

    def __init__(self) -> None:
        super().__init__()

        # ── Inicialización del backend ────────────────────────────────────────
        db.init_db()
        self._ui_queue: queue.Queue = queue.Queue()
        self._download_lock = threading.Lock()
        self._running_download = False
        self._stop_event = threading.Event()  # Para cancelar descarga en curso

        # ── Scheduler ────────────────────────────────────────────────────────
        self._scheduler = DropiScheduler(on_trigger=self._on_scheduler_trigger)
        self._scheduler.update_schedule(cfg.get_horarios())

        # ── Ventana ───────────────────────────────────────────────────────────
        self.title(f"{self.APP_NAME} v{self.APP_VERSION}")
        self.geometry(self.WIN_SIZE)
        self.minsize(*self.WIN_MIN)
        self.configure(fg_color=C_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── UI ───────────────────────────────────────────────────────────────
        self._build_header()
        self._build_tabs()
        self._build_status_bar()

        # ── Iniciar polling de la cola ────────────────────────────────────────
        self._poll_queue()

    # ──────────────────────────────────────────────────────────────────────────
    #  HEADER
    # ──────────────────────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=0, height=58)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="🛒  Dropi Tracker",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=C_TEXT,
        ).pack(side="left", padx=24, pady=12)

        self._status_label = ctk.CTkLabel(
            header,
            text="● Listo",
            font=ctk.CTkFont(size=12),
            text_color=C_SUCCESS,
        )
        self._status_label.pack(side="right", padx=24)

    # ──────────────────────────────────────────────────────────────────────────
    #  TABS
    # ──────────────────────────────────────────────────────────────────────────

    def _build_tabs(self) -> None:
        self._tabs = ctk.CTkTabview(
            self,
            fg_color=C_BG,
            segmented_button_fg_color=C_SURFACE,
            segmented_button_selected_color=C_ACCENT,
            segmented_button_selected_hover_color="#3a7ae0",
            segmented_button_unselected_color=C_SURFACE,
            segmented_button_unselected_hover_color=C_SURFACE2,
            text_color=C_TEXT,
            text_color_disabled=C_MUTED,
        )
        self._tabs.pack(fill="both", expand=True, padx=0, pady=0)

        self._tab_config    = self._tabs.add("⚙  Configuración")
        self._tab_download  = self._tabs.add("⬇  Descargar")
        self._tab_dashboard = self._tabs.add("📊  Dashboard")
        self._tab_bodegas   = self._tabs.add("🏪  Bodegas")
        self._tab_historial = self._tabs.add("📋  Historial")

        self._build_config_tab(self._tab_config)
        self._build_download_tab(self._tab_download)
        self._build_dashboard_tab(self._tab_dashboard)
        self._build_bodegas_tab(self._tab_bodegas)
        self._build_historial_tab(self._tab_historial)

    # ──────────────────────────────────────────────────────────────────────────
    #  TAB 1: CONFIGURACIÓN
    # ──────────────────────────────────────────────────────────────────────────

    def _build_config_tab(self, tab) -> None:
        tab.configure(fg_color=C_BG)
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C_BG)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Credenciales Dropi ────────────────────────────────────────────────
        token_card = self._card(scroll, "🔑  Credenciales de Dropi")

        ctk.CTkLabel(token_card, text="Correo electrónico:", text_color=C_MUTED).pack(anchor="w", padx=20, pady=(0, 4))
        self._e_email = ctk.CTkEntry(
            token_card, width=420,
            placeholder_text="tucorreo@ejemplo.com",
            font=ctk.CTkFont(size=13),
        )
        self._e_email.pack(anchor="w", padx=20, pady=(0, 10))
        self._e_email.insert(0, cfg.get_email())

        ctk.CTkLabel(token_card, text="Contraseña:", text_color=C_MUTED).pack(anchor="w", padx=20, pady=(0, 4))

        pwd_row = ctk.CTkFrame(token_card, fg_color="transparent")
        pwd_row.pack(anchor="w", padx=20, pady=(0, 14))

        self._e_password = ctk.CTkEntry(
            pwd_row, width=420, show="•",
            placeholder_text="Tu contraseña de Dropi",
            font=ctk.CTkFont(size=13),
        )
        self._e_password.pack(side="left", padx=(0, 10))
        self._e_password.insert(0, cfg.get_password())

        ctk.CTkButton(
            pwd_row, text="👁 Mostrar", width=90,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            command=self._toggle_password_visibility,
        ).pack(side="left")

        btn_login_row = ctk.CTkFrame(token_card, fg_color="transparent")
        btn_login_row.pack(anchor="w", padx=20, pady=(0, 16))

        ctk.CTkButton(
            btn_login_row, text="💾 Guardar credenciales", width=180,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            font=ctk.CTkFont(size=13),
            command=self._save_credentials,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_login_row, text="🔐 Probar conexión", width=160,
            fg_color=C_ACCENT, hover_color="#3a7ae0",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._run_in_thread(self._test_login),
        ).pack(side="left")

        self._login_status = ctk.CTkLabel(
            token_card, text="", text_color=C_MUTED,
            font=ctk.CTkFont(size=12),
        )
        self._login_status.pack(anchor="w", padx=20, pady=(0, 12))

        # ── Bodegas ───────────────────────────────────────────────────────────
        bod_card = self._card(scroll, "🏭  Bodegas a vigilar")

        # Treeview de bodegas
        tree_frame = ctk.CTkFrame(bod_card, fg_color="#0d1117", corner_radius=8)
        tree_frame.pack(fill="x", padx=20, pady=(0, 12))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Bodega.Treeview",
            background="#131629",
            foreground="#e8eaf6",
            fieldbackground="#131629",
            rowheight=30,
            font=("Segoe UI", 12),
        )
        style.configure(
            "Bodega.Treeview.Heading",
            background="#0d1117",
            foreground="#546e7a",
            font=("Segoe UI", 12, "bold"),
        )
        style.map("Bodega.Treeview", background=[("selected", "#252840")])

        cols = ("nombre", "url", "supplier_id")
        self._bodega_tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="headings",
            height=8,
            style="Bodega.Treeview",
        )
        self._bodega_tree.heading("nombre", text="Nombre bodega")
        self._bodega_tree.heading("url", text="URL")
        self._bodega_tree.heading("supplier_id", text="Supplier ID")
        self._bodega_tree.column("nombre", width=220)
        self._bodega_tree.column("url", width=380)
        self._bodega_tree.column("supplier_id", width=110, anchor="center")

        sb_bod = ttk.Scrollbar(tree_frame, orient="vertical", command=self._bodega_tree.yview)
        self._bodega_tree.configure(yscrollcommand=sb_bod.set)
        self._bodega_tree.pack(side="left", fill="x", expand=True)
        sb_bod.pack(side="right", fill="y")

        # Botones bodegas
        btn_frame = ctk.CTkFrame(bod_card, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))

        ctk.CTkButton(
            btn_frame, text="➕ Agregar", width=120,
            fg_color=C_SUCCESS, hover_color="#388e3c",
            command=self._agregar_bodega,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="📥 Importar Excel", width=140,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            command=self._importar_bodegas_excel,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="✏️ Editar", width=110,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            command=self._editar_bodega,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="🗑 Eliminar", width=110,
            fg_color=C_ERROR, hover_color="#c62828",
            command=self._eliminar_bodega,
        ).pack(side="left")

        self._refresh_bodega_tree()

        # ── Horarios automáticos ──────────────────────────────────────────────
        hor_card = self._card(scroll, "🕐  Horario automático")

        hor_desc = ctk.CTkLabel(
            hor_card,
            text="La aplicación descargará automáticamente en los horarios seleccionados:",
            text_color=C_MUTED, font=ctk.CTkFont(size=12),
        )
        hor_desc.pack(anchor="w", padx=20, pady=(0, 10))

        hor_row = ctk.CTkFrame(hor_card, fg_color="transparent")
        hor_row.pack(anchor="w", padx=20, pady=(0, 16))

        horarios = cfg.get_horarios()
        self._hor_vars: dict[str, ctk.BooleanVar] = {}

        for time_str in ["08:00", "12:00", "18:00", "00:00"]:
            var = ctk.BooleanVar(value=horarios.get(time_str, True))
            self._hor_vars[time_str] = var
            cb = ctk.CTkCheckBox(
                hor_row,
                text=time_str,
                variable=var,
                font=ctk.CTkFont(size=14),
                fg_color=C_ACCENT,
                hover_color="#3a7ae0",
                command=self._save_horarios,
            )
            cb.pack(side="left", padx=14)

        ctk.CTkLabel(
            hor_card,
            text="💡 Los cambios se aplican inmediatamente. La app debe estar abierta.",
            text_color=C_MUTED, font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=20, pady=(0, 12))

    # ──────────────────────────────────────────────────────────────────────────
    #  TAB 2: DESCARGAR
    # ──────────────────────────────────────────────────────────────────────────

    def _build_download_tab(self, tab) -> None:
        tab.configure(fg_color=C_BG)

        # Botones superiores
        btn_card = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=12)
        btn_card.pack(fill="x", padx=20, pady=(16, 0))

        btn_inner = ctk.CTkFrame(btn_card, fg_color="transparent")
        btn_inner.pack(padx=20, pady=14)

        self._btn_download = ctk.CTkButton(
            btn_inner, text="⬇  Descargar ahora", width=200, height=42,
            fg_color=C_ACCENT, hover_color="#3a7ae0",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_download,
        )
        self._btn_download.pack(side="left", padx=(0, 10))

        self._btn_stop = ctk.CTkButton(
            btn_inner, text="⛔  Detener", width=130, height=42,
            fg_color=C_ERROR, hover_color="#c62828",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._stop_download,
            state="disabled",
        )
        self._btn_stop.pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_inner, text="📤  Exportar Excel", width=160, height=42,
            fg_color="#2e7d32", hover_color="#1b5e20",
            font=ctk.CTkFont(size=14),
            command=self._export_all,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_inner, text="📊  Reporte Histórico", width=180, height=42,
            fg_color="#7b1fa2", hover_color="#4a148c",
            font=ctk.CTkFont(size=14),
            command=lambda: self._run_in_thread(self._export_reporte_historico),
        ).pack(side="left")

        # Progreso
        progress_frame = ctk.CTkFrame(tab, fg_color="transparent")
        progress_frame.pack(fill="x", padx=20, pady=(10, 0))

        self._progress_bar = ctk.CTkProgressBar(
            progress_frame, mode="indeterminate",
            progress_color=C_ACCENT, fg_color=C_SURFACE2, height=6,
        )
        self._progress_bar.pack(fill="x")
        self._progress_bar.set(0)

        # Log
        log_card = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=12)
        log_card.pack(fill="both", expand=True, padx=20, pady=12)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.pack(fill="x", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            log_header,
            text="📝 Registro de actividad",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=C_TEXT,
        ).pack(side="left")

        ctk.CTkButton(
            log_header, text="🗑 Limpiar", width=90, height=28,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            font=ctk.CTkFont(size=12),
            command=self._clear_log,
        ).pack(side="right")

        self._log_box = ctk.CTkTextbox(
            log_card,
            fg_color="#0d1117",
            text_color="#b0bec5",
            font=ctk.CTkFont(family="Consolas", size=12),
            corner_radius=8,
            wrap="word",
        )
        self._log_box.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._log_box.configure(state="disabled")

    # ──────────────────────────────────────────────────────────────────────────
    #  TAB 3: DASHBOARD
    # ──────────────────────────────────────────────────────────────────────────

    def _build_dashboard_tab(self, tab) -> None:
        tab.configure(fg_color=C_BG)

        inner_tabs = ctk.CTkTabview(
            tab,
            fg_color=C_BG,
            segmented_button_fg_color=C_SURFACE,
            segmented_button_selected_color=C_ACCENT,
            segmented_button_selected_hover_color="#3a7ae0",
            segmented_button_unselected_color=C_SURFACE,
            text_color=C_TEXT,
        )
        inner_tabs.pack(fill="both", expand=True, padx=0, pady=0)

        tab_inv    = inner_tabs.add("📦  Inventario")
        tab_hot    = inner_tabs.add("🔥  Más Vendidos")

        self._build_inventory_view(tab_inv)
        self._build_hot_products_view(tab_hot)

    def _build_inventory_view(self, tab) -> None:
        tab.configure(fg_color=C_BG)

        top_bar = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=10)
        top_bar.pack(fill="x", padx=16, pady=(14, 8))

        top_inner = ctk.CTkFrame(top_bar, fg_color="transparent")
        top_inner.pack(padx=16, pady=12, fill="x")

        ctk.CTkLabel(
            top_inner,
            text="Seleccione una bodega:",
            font=ctk.CTkFont(size=13),
            text_color=C_MUTED,
        ).pack(side="left", padx=(0, 10))

        self._bodega_combo = ctk.CTkComboBox(
            top_inner, width=200,
            font=ctk.CTkFont(size=13),
            dropdown_fg_color=C_SURFACE2,
            button_color=C_ACCENT,
            command=self._on_bodega_select,
        )
        self._bodega_combo.pack(side="left", padx=(0, 10))

        # Barra de búsqueda
        self._search_entry = ctk.CTkEntry(
            top_inner, width=180,
            placeholder_text="🔍 Buscar nombre o SKU...",
            font=ctk.CTkFont(size=13),
        )
        self._search_entry.pack(side="left", padx=(0, 10))
        self._search_entry.bind("<KeyRelease>", self._on_search_change)

        # Filtro de variación
        self._filter_combo = ctk.CTkComboBox(
            top_inner, width=110,
            values=["Todos", "Bajaron", "Subieron"],
            font=ctk.CTkFont(size=13),
            dropdown_fg_color=C_SURFACE2,
            button_color=C_ACCENT,
            command=self._on_filter_change,
        )
        self._filter_combo.pack(side="left", padx=(0, 10))
        self._filter_combo.set("Todos")

        # Filtro de período
        ctk.CTkLabel(
            top_inner,
            text="Período:",
            text_color=C_MUTED, font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))

        self._inventory_period = ctk.CTkComboBox(
            top_inner, width=140,
            values=["Todo el historial", "Últimas 24h", "Últimas 48h", "Últimas 72h", "Última semana", "Últimos 30 días"],
            font=ctk.CTkFont(size=13),
            dropdown_fg_color=C_SURFACE2,
            button_color=C_ACCENT,
            command=self._refresh_dashboard,
        )
        self._inventory_period.pack(side="left", padx=(0, 10))
        self._inventory_period.set("Todo el historial")

        ctk.CTkLabel(
            top_inner,
            text="Ordenar:",
            text_color=C_MUTED, font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))

        self._inventory_sort = ctk.CTkComboBox(
            top_inner, width=140,
            values=["Cambios (desc)", "Cambios (asc)", "Nombre (A-Z)", "Nombre (Z-A)", "Stock (desc)", "Stock (asc)"],
            font=ctk.CTkFont(size=13),
            dropdown_fg_color=C_SURFACE2,
            button_color=C_ACCENT,
            command=self._refresh_dashboard,
        )
        self._inventory_sort.pack(side="left", padx=(0, 10))
        self._inventory_sort.set("Cambios (desc)")

        ctk.CTkButton(
            top_inner, text="🔄 Actualizar", width=100, height=32,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            command=self._refresh_dashboard,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            top_inner, text="📤 Excel", width=80, height=32,
            fg_color="#2e7d32", hover_color="#1b5e20",
            command=self._export_inventario,
        ).pack(side="left")

        # Estadísticas rápidas
        self._stats_frame = ctk.CTkFrame(top_inner, fg_color="transparent")
        self._stats_frame.pack(side="right", padx=(20, 0))

        self._stat_total  = self._stat_chip(self._stats_frame, "Total", "—", C_ACCENT)
        self._stat_bajos  = self._stat_chip(self._stats_frame, "Bajaron", "—", C_ERROR)
        self._stat_subieron = self._stat_chip(self._stats_frame, "Subieron", "—", C_SUCCESS)

        # Tabla de productos
        self._dashboard_table = DashboardTable(tab)
        self._dashboard_table.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self._dashboard_table.set_on_click(self._open_product_detail)

        self._refresh_bodega_combo()

    def _stat_chip(self, parent, label: str, value: str, color: str):
        frame = ctk.CTkFrame(parent, fg_color=C_SURFACE2, corner_radius=8)
        frame.pack(side="left", padx=4)
        ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(size=10), text_color=C_MUTED).pack(padx=10, pady=(4, 0))
        lbl = ctk.CTkLabel(frame, text=value, font=ctk.CTkFont(size=16, weight="bold"), text_color=color)
        lbl.pack(padx=10, pady=(0, 6))
        return lbl

    def _build_hot_products_view(self, tab) -> None:
        tab.configure(fg_color=C_BG)

        top = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=10)
        top.pack(fill="x", padx=16, pady=(14, 8))

        top_inner = ctk.CTkFrame(top, fg_color="transparent")
        top_inner.pack(padx=16, pady=12, fill="x")

        ctk.CTkLabel(
            top_inner,
            text="Período:",
            text_color=C_MUTED, font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))

        self._hot_period = ctk.CTkComboBox(
            top_inner, width=160,
            values=["Últimas 24h", "Últimas 48h", "Últimas 72h", "Última semana", "Últimos 30 días", "Todo el historial"],
            font=ctk.CTkFont(size=13),
            dropdown_fg_color=C_SURFACE2,
        )
        self._hot_period.pack(side="left", padx=(0, 12))
        self._hot_period.set("Últimas 24h")

        ctk.CTkLabel(
            top_inner,
            text="Ordenar:",
            text_color=C_MUTED, font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))

        self._hot_sort = ctk.CTkComboBox(
            top_inner, width=140,
            values=["Ventas (desc)", "Ventas (asc)", "Nombre (A-Z)", "Nombre (Z-A)", "Bodega (A-Z)"],
            font=ctk.CTkFont(size=13),
            dropdown_fg_color=C_SURFACE2,
            button_color=C_ACCENT,
            command=self._refresh_hot,
        )
        self._hot_sort.pack(side="left", padx=(0, 12))
        self._hot_sort.set("Ventas (desc)")

        ctk.CTkButton(
            top_inner, text="🔄 Calcular", width=110, height=32,
            fg_color=C_ACCENT, hover_color="#3a7ae0",
            command=self._refresh_hot,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            top_inner, text="📤 Excel", width=100, height=32,
            fg_color="#2e7d32", hover_color="#1b5e20",
            command=lambda: self._run_in_thread(self._export_calientes),
        ).pack(side="left")

        # Tabla hot products (nueva — con imagen y SKU)
        self._hot_table = HotDashboardTable(tab)
        self._hot_table.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self._hot_table.set_on_click(self._open_hot_product_detail)

        self._refresh_hot()

    # ──────────────────────────────────────────────────────────────────────────
    #  TAB 4: HISTORIAL DE ACTIVIDAD
    # ──────────────────────────────────────────────────────────────────────────

    def _build_historial_tab(self, tab) -> None:
        tab.configure(fg_color=C_BG)

        top = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=10)
        top.pack(fill="x", padx=16, pady=(14, 8))

        top_inner = ctk.CTkFrame(top, fg_color="transparent")
        top_inner.pack(padx=16, pady=12, fill="x")

        ctk.CTkLabel(
            top_inner,
            text="📋 Log de descargas automáticas y manuales",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=C_TEXT,
        ).pack(side="left")

        ctk.CTkButton(
            top_inner, text="🔄 Actualizar", width=110, height=30,
            fg_color=C_SURFACE2, hover_color="#2e3250",
            command=self._refresh_historial_tab,
        ).pack(side="right")

        # Tabla log
        table_frame = ctk.CTkFrame(tab, fg_color=C_SURFACE, corner_radius=10)
        table_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        style = ttk.Style()
        style.configure(
            "Log.Treeview",
            background="#1b2036",
            foreground="#e8eaf6",
            fieldbackground="#1b2036",
            rowheight=28,
            font=("Segoe UI", 12),
        )
        style.configure(
            "Log.Treeview.Heading",
            background="#0d1117",
            foreground="#546e7a",
            font=("Segoe UI", 12, "bold"),
        )
        style.map("Log.Treeview", background=[("selected", "#252840")])

        self._log_tree = ttk.Treeview(
            table_frame,
            columns=("fecha", "hora", "bodega", "productos", "errores"),
            show="headings",
            style="Log.Treeview",
        )
        self._log_tree.heading("fecha",     text="Fecha")
        self._log_tree.heading("hora",      text="Hora")
        self._log_tree.heading("bodega",    text="Bodega")
        self._log_tree.heading("productos", text="Productos")
        self._log_tree.heading("errores",   text="Errores")

        self._log_tree.column("fecha",     width=110, anchor="center")
        self._log_tree.column("hora",      width=80,  anchor="center")
        self._log_tree.column("bodega",    width=200)
        self._log_tree.column("productos", width=110, anchor="center")
        self._log_tree.column("errores",   width=300)

        sb_log = ttk.Scrollbar(table_frame, orient="vertical", command=self._log_tree.yview)
        self._log_tree.configure(yscrollcommand=sb_log.set)
        self._log_tree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb_log.pack(side="right", fill="y")

        self._refresh_historial_tab()

    # ──────────────────────────────────────────────────────────────────────────
    #  TAB 4: BODEGAS
    # ──────────────────────────────────────────────────────────────────────────

    def _build_bodegas_tab(self, tab) -> None:
        tab.configure(fg_color=C_BG)
        scroll = ctk.CTkScrollableFrame(tab, fg_color=C_BG)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Header con botón de sincronización ─────────────────────────────────
        header_card = self._card(scroll, "🏪  Directorio de Bodegas")
        
        top_inner = ctk.CTkFrame(header_card, fg_color=C_SURFACE2, corner_radius=8)
        top_inner.pack(fill="x", padx=16, pady=(0, 12))
        
        ctk.CTkLabel(
            top_inner,
            text="Proveedores y bodegas sincronizadas desde Dropi",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=C_TEXT,
        ).pack(side="left", padx=12, pady=8)

        # Campo de búsqueda por nombre
        search_frame = ctk.CTkFrame(top_inner, fg_color="transparent")
        search_frame.pack(side="left", padx=(12, 8), pady=8)
        
        ctk.CTkLabel(
            search_frame,
            text="🔍",
            font=ctk.CTkFont(size=14),
            text_color=C_MUTED,
        ).pack(side="left", padx=(0, 4))
        
        self._bodegas_search = ctk.CTkEntry(
            search_frame,
            width=200,
            height=32,
            placeholder_text="Buscar por nombre...",
            font=ctk.CTkFont(size=12),
        )
        self._bodegas_search.pack(side="left")
        self._bodegas_search.bind("<KeyRelease>", self._filter_bodegas_by_name)

        ctk.CTkButton(
            top_inner, 
            text="🔄 Sincronizar desde Dropi", 
            width=180, 
            height=32,
            fg_color=C_ACCENT, 
            hover_color="#3a7ae0",
            command=self._sync_suppliers_worker
        ).pack(side="right", padx=(8, 12), pady=8)

        ctk.CTkButton(
            top_inner, 
            text="📥 Exportar a Excel", 
            width=150, 
            height=32,
            fg_color=C_SUCCESS, 
            hover_color="#43a047",
            command=self._export_bodegas
        ).pack(side="right", padx=(12, 8), pady=8)

        # Botón de eliminar con menú
        delete_menu = ctk.CTkOptionMenu(
            top_inner,
            values=["Eliminar seleccionadas", "Eliminar todas"],
            width=130,
            height=32,
            fg_color=C_ERROR,
            button_color=C_ERROR,
            button_hover_color="#d32f2f",
            dropdown_fg_color="#1b2036",
            dropdown_hover_color="#252840",
            command=self._delete_supplier
        )
        delete_menu.set("Eliminar seleccionadas")
        delete_menu.pack(side="right", padx=(8, 12), pady=8)

        # Botón para marcar todas en seguimiento
        ctk.CTkButton(
            top_inner,
            text="Marcar todas",
            width=110,
            height=32,
            fg_color=C_WARNING,
            hover_color="#f57c00",
            command=self._mark_all_seguimiento
        ).pack(side="right", padx=(8, 8), pady=8)

        # Botón para extraer órdenes
        ctk.CTkButton(
            top_inner,
            text="Extraer Órdenes",
            width=130,
            height=32,
            fg_color="#9c27b0",
            hover_color="#7b1fa2",
            command=self._extract_ordenes_worker
        ).pack(side="right", padx=(8, 8), pady=8)

        # Botón para ver solo seguimiento
        self._filter_seguimiento_btn = ctk.CTkButton(
            top_inner,
            text="Solo seguimiento",
            width=130,
            height=32,
            fg_color="#546e7a",
            hover_color="#607d8b",
            command=self._toggle_seguimiento_filter
        )
        self._filter_seguimiento_btn.pack(side="right", padx=(8, 8), pady=8)
        self._showing_seguimiento_only = False
        self._sort_column = None
        self._sort_reverse = False

        # ── Tabla de bodegas ───────────────────────────────────────────────────
        table_frame = ctk.CTkFrame(scroll, fg_color=C_SURFACE, corner_radius=10)
        table_frame.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        style = ttk.Style()
        style.configure(
            "Bodegas.Treeview",
            background="#1b2036",
            foreground="#e8eaf6",
            fieldbackground="#1b2036",
            rowheight=28,
            font=("Segoe UI", 11),
        )
        style.configure(
            "Bodegas.Treeview.Heading",
            background="#0d1117",
            foreground="#546e7a",
            font=("Segoe UI", 11, "bold"),
        )
        style.map("Bodegas.Treeview", background=[("selected", "#252840")])

        self._bodegas_tree = ttk.Treeview(
            table_frame,
            columns=("empresa", "nombre", "direccion", "ciudad", "celular", "productos", "ordenes", "dropshippers", "seguimiento", "url"),
            show="headings",
            style="Bodegas.Treeview",
        )
        self._bodegas_tree.heading("empresa",   text="Empresa", command=lambda: self._sort_bodegas("empresa"))
        self._bodegas_tree.heading("nombre",    text="Bodega", command=lambda: self._sort_bodegas("nombre"))
        self._bodegas_tree.heading("direccion", text="Dirección", command=lambda: self._sort_bodegas("direccion"))
        self._bodegas_tree.heading("ciudad",    text="Ciudad", command=lambda: self._sort_bodegas("ciudad"))
        self._bodegas_tree.heading("celular",   text="Celular", command=lambda: self._sort_bodegas("celular"))
        self._bodegas_tree.heading("productos", text="Productos", command=lambda: self._sort_bodegas("productos"))
        self._bodegas_tree.heading("ordenes",   text="Órdenes", command=lambda: self._sort_bodegas("ordenes"))
        self._bodegas_tree.heading("dropshippers", text="Dropshippers", command=lambda: self._sort_bodegas("dropshippers"))
        self._bodegas_tree.heading("seguimiento", text="Seguimiento", command=lambda: self._sort_bodegas("seguimiento"))
        self._bodegas_tree.heading("url",       text="URL Descarga", command=lambda: self._sort_bodegas("url"))

        self._bodegas_tree.column("empresa",   width=160)
        self._bodegas_tree.column("nombre",    width=130)
        self._bodegas_tree.column("direccion", width=180)
        self._bodegas_tree.column("ciudad",    width=90)
        self._bodegas_tree.column("celular",   width=110)
        self._bodegas_tree.column("productos", width=80)
        self._bodegas_tree.column("ordenes",   width=80)
        self._bodegas_tree.column("dropshippers", width=90)
        self._bodegas_tree.column("seguimiento", width=70)
        self._bodegas_tree.column("url",       width=220)

        sb_bodegas = ttk.Scrollbar(table_frame, orient="vertical", command=self._bodegas_tree.yview)
        self._bodegas_tree.configure(yscrollcommand=sb_bodegas.set)
        self._bodegas_tree.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb_bodegas.pack(side="right", fill="y")

        self._bodegas_tree.bind("<Double-1>", self._on_bodega_double_click)

        self._refresh_bodegas_tab()

    def _refresh_bodegas_tab(self) -> None:
        """Carga las bodegas desde la base de datos en la tabla."""
        # Limpiar tabla
        for item in self._bodegas_tree.get_children():
            self._bodegas_tree.delete(item)

        warehouses = db.get_warehouses()

        # Filtrar por seguimiento si está activo
        if self._showing_seguimiento_only:
            warehouses = [wh for wh in warehouses if wh.get("seguimiento", 0)]

        # Filtrar por nombre de búsqueda
        search_text = self._bodegas_search.get().lower().strip()
        if search_text:
            warehouses = [wh for wh in warehouses 
                         if search_text in wh.get("warehouse_name", "").lower() 
                         or search_text in wh.get("supplier_name", "").lower()]

        # Aplicar ordenamiento si hay una columna seleccionada
        if self._sort_column:
            column_map = {
                "empresa": "supplier_name",
                "nombre": "warehouse_name",
                "direccion": "address",
                "ciudad": "city_name",
                "celular": "phone",
                "productos": "productos_count",
                "ordenes": "ordenes",
                "dropshippers": "dropshippers",
                "seguimiento": "seguimiento",
                "url": None  # URL se calcula, no se ordena por esto
            }

            db_field = column_map.get(self._sort_column)
            if db_field:
                reverse = self._sort_reverse
                if db_field in ["productos_count", "ordenes", "dropshippers"]:
                    # Ordenamiento numérico para productos, órdenes y dropshippers
                    warehouses.sort(key=lambda x: x.get(db_field, 0) or 0, reverse=reverse)
                elif db_field == "seguimiento":
                    # Ordenamiento booleano para seguimiento
                    warehouses.sort(key=lambda x: x.get(db_field, 0) or 0, reverse=reverse)
                else:
                    # Ordenamiento alfabético para texto
                    warehouses.sort(key=lambda x: str(x.get(db_field, "")).lower(), reverse=reverse)

        for wh in warehouses:
            # URL con formato: https://app.dropi.co/dashboard/provider/{supplier_id}/{nombre_propietario}
            owner_name = wh.get("owner_name", wh.get("supplier_name", ""))
            # Limpiar el nombre para URL (reemplazar espacios y caracteres especiales)
            owner_name_clean = owner_name.replace(" ", "-").replace("/", "-").replace("\\", "-")
            url = f"https://app.dropi.co/dashboard/provider/{wh['supplier_id']}/{owner_name_clean}"

            # Obtener conteo de productos
            productos_count = wh.get("productos_count", 0)

            self._bodegas_tree.insert(
                "",
                "end",
                values=(
                    wh.get("supplier_name", ""),
                    wh.get("warehouse_name", ""),
                    wh.get("address", ""),
                    wh.get("city_name", ""),
                    wh.get("phone", ""),
                    productos_count,
                    wh.get("ordenes", 0),
                    wh.get("dropshippers", 0),
                    "✓" if wh.get("seguimiento", 0) else "",
                    url,
                ),
                tags=(wh.get("warehouse_id"),),
            )

    def _filter_bodegas_by_name(self, event=None) -> None:
        """Filtra las bodegas por nombre cuando el usuario escribe en el campo de búsqueda."""
        self._refresh_bodegas_tab()

    # ──────────────────────────────────────────────────────────────────────────
    #  ACCIONES: BODEGAS TAB
    # ──────────────────────────────────────────────────────────────────────────

    def _on_bodega_double_click(self, event) -> None:
        """Maneja el doble clic en una bodega para alternar seguimiento."""
        item_id = self._bodegas_tree.identify("item", event.x, event.y)
        if not item_id:
            return

        item = self._bodegas_tree.item(item_id)
        tags = item.get("tags", [])
        if not tags:
            return

        warehouse_id = tags[0]
        try:
            warehouse_id = int(warehouse_id)
        except ValueError:
            return

        if db.toggle_warehouse_seguimiento(warehouse_id):
            self._log(f"✓ Seguimiento alternado para bodega {warehouse_id}")
            self._refresh_bodegas_tab()
        else:
            self._log(f"🔴 Error al alternar seguimiento para bodega {warehouse_id}")

    def _sort_bodegas(self, column: str) -> None:
        """Ordena la tabla por la columna especificada."""
        # Si es la misma columna, invertir el orden
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            # Para productos, por defecto ordenar descendente (mayor a menor)
            self._sort_reverse = (column == "productos")

        # Actualizar encabezados con indicador de ordenamiento
        column_names = ["empresa", "nombre", "direccion", "ciudad", "celular", "productos", "ordenes", "dropshippers", "seguimiento", "url"]
        for col in column_names:
            if col == self._sort_column:
                indicator = " ▼" if self._sort_reverse else " ▲"
                text_map = {
                    "empresa": "Empresa",
                    "nombre": "Bodega",
                    "direccion": "Dirección",
                    "ciudad": "Ciudad",
                    "celular": "Celular",
                    "productos": "Productos",
                    "ordenes": "Órdenes",
                    "dropshippers": "Dropshippers",
                    "seguimiento": "Seguimiento",
                    "url": "URL Descarga"
                }
                self._bodegas_tree.heading(col, text=text_map[col] + indicator)
            else:
                text_map = {
                    "empresa": "Empresa",
                    "nombre": "Bodega",
                    "direccion": "Dirección",
                    "ciudad": "Ciudad",
                    "celular": "Celular",
                    "productos": "Productos",
                    "ordenes": "Órdenes",
                    "dropshippers": "Dropshippers",
                    "seguimiento": "Seguimiento",
                    "url": "URL Descarga"
                }
                self._bodegas_tree.heading(col, text=text_map[col])

        self._refresh_bodegas_tab()

    def _mark_all_seguimiento(self) -> None:
        """Marca todas las bodegas visibles como en seguimiento."""
        warehouses = db.get_warehouses()
        if not warehouses:
            messagebox.showinfo("Aviso", "No hay bodegas para marcar.", parent=self)
            return

        confirm = messagebox.askyesno(
            "Confirmar",
            f"¿Marcar todas las bodegas ({len(warehouses)}) como en seguimiento?",
            parent=self
        )
        if not confirm:
            return

        marcadas = 0
        for wh in warehouses:
            if db.set_warehouse_seguimiento(wh["warehouse_id"], True):
                marcadas += 1

        self._log(f"✓ Marcadas {marcadas} bodegas en seguimiento")
        self._refresh_bodegas_tab()
        messagebox.showinfo("Completado", f"{marcadas} bodegas marcadas en seguimiento.", parent=self)

    def _toggle_seguimiento_filter(self) -> None:
        """Alterna el filtro para mostrar solo bodegas en seguimiento."""
        self._showing_seguimiento_only = not self._showing_seguimiento_only
        if self._showing_seguimiento_only:
            self._filter_seguimiento_btn.configure(fg_color=C_SUCCESS, hover_color="#43a047", text="Ver todas")
            self._log("🔍 Filtro activo: Solo bodegas en seguimiento")
        else:
            self._filter_seguimiento_btn.configure(fg_color="#546e7a", hover_color="#607d8b", text="Solo seguimiento")
            self._log("🔍 Filtro desactivado: Mostrar todas las bodegas")
        self._refresh_bodegas_tab()

    def _extract_ordenes_worker(self) -> None:
        """Worker para extraer órdenes de todas las bodegas en hilo separado."""
        if self._running_download:
            messagebox.showwarning("Operación en curso", "Ya hay una operación en curso.")
            return

        warehouses = db.get_warehouses()
        if not warehouses:
            messagebox.showinfo("Aviso", "No hay bodegas para procesar.", parent=self)
            return

        confirm = messagebox.askyesno(
            "Confirmar",
            f"¿Extraer órdenes de {len(warehouses)} bodegas?\n\nEsta operación puede tardar varios minutos.",
            parent=self
        )
        if not confirm:
            return

        self._running_download = True
        self._stop_event.clear()
        self._status_label.configure(text="● Extrayendo órdenes...", text_color=C_WARNING)

        def worker():
            def progress_callback(msg: str, actual: int, total: int):
                self._ui_queue.put(("log", msg))

            try:
                exitosas, fallidas = db.sync_ordenes_bodegas(progress_callback)
                self._ui_queue.put(("ordenes_complete", (exitosas, fallidas)))
            except Exception as e:
                self._ui_queue.put(("error", str(e)))
            finally:
                self._running_download = False
                self._ui_queue.put(("status", "Listo"))

        threading.Thread(target=worker, daemon=True).start()

    def _sync_suppliers_worker(self) -> None:
        """Worker para sincronizar proveedores desde Dropi en hilo separado."""
        if self._running_download:
            messagebox.showwarning("Descarga en curso", "Ya hay una operación en curso.")
            return

        token = cfg.get_token()
        if not token:
            messagebox.showerror("Error", "No hay token configurado. Ve a Configuración.")
            return

        self._running_download = True
        self._stop_event.clear()
        self._status_label.configure(text="● Sincronizando...", text_color=C_WARNING)

        def worker():
            from api_dropi import obtener_proveedores_dropi

            all_proveedores = []
            nuevos_sup_total = 0
            actualizados_sup_total = 0
            saltados_sup_total = 0
            nuevas_wh_total = 0
            actualizadas_wh_total = 0

            def log_callback(msg: str, lote: Optional[List[dict]] = None):
                self._ui_queue.put(("log", msg))

                # Sincronizar incrementalmente si hay lote
                if lote:
                    all_proveedores.extend(lote)
                    try:
                        nuevos_sup, actualizados_sup, saltados_sup = db.sync_dropi_suppliers(lote)
                        nuevas_wh, actualizadas_wh = db.sync_dropi_warehouses(lote)

                        nuevos_sup_total += nuevos_sup
                        actualizados_sup_total += actualizados_sup
                        saltados_sup_total += saltados_sup
                        nuevas_wh_total += nuevas_wh
                        actualizadas_wh_total += actualizadas_wh

                        # Actualizar tabla en tiempo real
                        self._ui_queue.put(("refresh_bodegas",))
                    except Exception as e:
                        self._ui_queue.put(("log", f"⚠ Error sincronizando lote: {e}"))

            try:
                proveedores = obtener_proveedores_dropi(
                    token=token,
                    callback=log_callback,
                    stop_event=self._stop_event
                )

                if proveedores:
                    self._ui_queue.put(("sync_complete", (nuevos_sup_total, actualizados_sup_total, saltados_sup_total, nuevas_wh_total, actualizadas_wh_total)))
                else:
                    self._ui_queue.put(("error", "No se obtuvieron proveedores de Dropi"))

            except Exception as e:
                self._ui_queue.put(("error", str(e)))
            finally:
                self._running_download = False
                self._ui_queue.put(("status", "Listo"))

        threading.Thread(target=worker, daemon=True).start()

    def _export_bodegas(self) -> None:
        """Exporta la lista de bodegas y proveedores a Excel."""
        try:
            self._log("📤 Exportando bodegas y proveedores a Excel…")
            path = exporter.exportar_bodegas()
            self._log(f"✅ Excel guardado: {path}")
            os.startfile(path)
        except Exception as exc:
            self._log(f"🔴 Error al exportar bodegas: {exc}")
            messagebox.showerror("Error", f"No se pudo exportar bodegas: {exc}")

    def _delete_supplier(self, option: str) -> None:
        """Elimina proveedores según la opción seleccionada."""
        if option == "Eliminar todas":
            self._delete_all_suppliers()
        elif option == "Eliminar seleccionadas":
            self._delete_selected_suppliers()

    def _delete_all_suppliers(self) -> None:
        """Elimina todos los proveedores y bodegas."""
        warehouses = db.get_warehouses()
        if not warehouses:
            messagebox.showinfo("Aviso", "No hay proveedores para eliminar.", parent=self)
            return

        total = len(set(wh['supplier_id'] for wh in warehouses))
        confirm_msg = f"¿Eliminar TODOS los proveedores ({total}) y sus bodegas?\n\nEsta acción no se puede deshacer."
        
        if messagebox.askyesno("Confirmar eliminación total", confirm_msg, parent=self):
            try:
                with db.get_connection() as conn:
                    conn.execute("DELETE FROM warehouses")
                    conn.execute("DELETE FROM suppliers")
                    conn.commit()
                
                self._log(f"🗑 Eliminados {total} proveedores y todas sus bodegas")
                self._refresh_bodegas_tab()
                messagebox.showinfo("Eliminado", f"Todos los proveedores ({total}) eliminados correctamente.", parent=self)
            except Exception as exc:
                self._log(f"🔴 Error al eliminar todos: {exc}")
                messagebox.showerror("Error", f"No se pudo eliminar todos: {exc}", parent=self)

    def _delete_selected_suppliers(self) -> None:
        """Elimina los proveedores seleccionados en la tabla."""
        sel = self._bodegas_tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecciona al menos una bodega/proveedor para eliminar.", parent=self)
            return

        # Obtener supplier_ids únicos de las filas seleccionadas
        supplier_ids = set()
        for item_id in sel:
            item = self._bodegas_tree.item(item_id)
            values = item["values"]
            url = values[6] if len(values) > 6 else ""
            
            if url and "/dashboard/provider/" in url:
                parts = url.split("/dashboard/provider/")
                if len(parts) > 1:
                    supplier_id = parts[1].split("/")[0]
                    try:
                        supplier_ids.add(int(supplier_id))
                    except ValueError:
                        pass

        if not supplier_ids:
            messagebox.showerror("Error", "No se pudo identificar los supplier_ids seleccionados.", parent=self)
            return

        empresas = []
        for item_id in sel[:3]:  # Mostrar hasta 3 nombres
            item = self._bodegas_tree.item(item_id)
            values = item["values"]
            empresas.append(values[0] if len(values) > 0 else "")
        
        empresas_str = ", ".join(empresas)
        if len(sel) > 3:
            empresas_str += f" y {len(sel) - 3} más"

        confirm_msg = f"¿Eliminar {len(supplier_ids)} proveedor(es): {empresas_str}?\n\nEsta acción no se puede deshacer."
        
        if messagebox.askyesno("Confirmar eliminación", confirm_msg, parent=self):
            try:
                eliminados = 0
                for supplier_id in supplier_ids:
                    if db.delete_supplier(supplier_id):
                        eliminados += 1
                
                self._log(f"🗑 Eliminados {eliminados} proveedores seleccionados")
                self._refresh_bodegas_tab()
                messagebox.showinfo("Eliminado", f"{eliminados} proveedor(es) eliminado(s) correctamente.", parent=self)
            except Exception as exc:
                self._log(f"🔴 Error al eliminar seleccionados: {exc}")
                messagebox.showerror("Error", f"No se pudo eliminar: {exc}", parent=self)

    # ──────────────────────────────────────────────────────────────────────────
    #  STATUS BAR
    # ──────────────────────────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        self._statusbar = ctk.CTkFrame(self, fg_color=C_SURFACE, corner_radius=0, height=28)
        self._statusbar.pack(fill="x", side="bottom")
        self._statusbar.pack_propagate(False)

        self._statusbar_label = ctk.CTkLabel(
            self._statusbar,
            text=f"  {self.APP_NAME} v{self.APP_VERSION}  |  Memoria: {db.DB_PATH}",
            font=ctk.CTkFont(size=11),
            text_color=C_MUTED,
        )
        self._statusbar_label.pack(side="left", padx=8)

    # ──────────────────────────────────────────────────────────────────────────
    #  HELPER: CARD
    # ──────────────────────────────────────────────────────────────────────────

    def _card(self, parent, title: str) -> ctk.CTkFrame:
        outer = ctk.CTkFrame(parent, fg_color=C_SURFACE, corner_radius=12)
        outer.pack(fill="x", padx=20, pady=(14, 0))
        ctk.CTkLabel(
            outer,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=C_TEXT,
        ).pack(anchor="w", padx=20, pady=(14, 8))
        return outer

    # ──────────────────────────────────────────────────────────────────────────
    #  ACCIONES: CONFIGURACIÓN
    # ──────────────────────────────────────────────────────────────────────────

    def _toggle_token_visibility(self) -> None:
        # Compatibilidad hacia atrás (no usado ya en la UI)
        pass

    def _toggle_password_visibility(self) -> None:
        current = self._e_password.cget("show")
        self._e_password.configure(show="" if current == "•" else "•")

    def _save_credentials(self) -> None:
        email = self._e_email.get().strip()
        password = self._e_password.get()
        if not email or not password:
            messagebox.showwarning("Aviso", "El correo y contraseña son obligatorios.", parent=self)
            return
        cfg.save_credentials(email, password)
        self._log("✓ Credenciales guardadas.")
        messagebox.showinfo("✓ Guardado", "Credenciales guardadas exitosamente.", parent=self)

    def _save_horarios(self) -> None:
        new_horarios = {time_str: var.get() for time_str, var in self._hor_vars.items()}
        cfg.save_horarios(new_horarios)
        self._scheduler.update_schedule(new_horarios)
        self._log("✓ Horarios actualizados en la configuración y el planificador.")

    def _test_login(self) -> None:
        email = self._e_email.get().strip()
        password = self._e_password.get()
        if not email or not password:
            self._ui_queue.put(lambda: self._login_status.configure(text="🔴 Ingresa correo y contraseña", text_color=C_ERROR))
            return

        self._ui_queue.put(lambda: self._login_status.configure(text="🔐 Probando conexión...", text_color=C_WARNING))
        try:
            token = login_dropi(email, password)
            cfg.save_token(token)
            self._ui_queue.put(lambda: self._login_status.configure(text="✅ Conexión exitosa. Token guardado.", text_color=C_SUCCESS))
        except Exception as exc:
            self._ui_queue.put(lambda: self._login_status.configure(text=f"🔴 Fallido: {exc}", text_color=C_ERROR))

    def _stop_download(self) -> None:
        """Señala al worker que cancele la descarga en curso."""
        if self._running_download:
            self._stop_event.set()
            self._log("⛔ Cancelando descarga… espera un momento.")
            self._ui_queue.put(lambda: self._btn_stop.configure(state="disabled", text="⏳ Cancelando…"))

    def _start_download(self) -> None:
        if self._running_download:
            messagebox.showinfo("Info", "Ya hay una descarga en progreso.", parent=self)
            return

        email = cfg.get_email()
        password = cfg.get_password()

        if not email or not password:
            messagebox.showerror(
                "Sin credenciales",
                "Configura tu correo y contraseña en la pestaña ⚙ Configuración.",
                parent=self,
            )
            self._tabs.set("⚙  Configuración")
            return

        # Obtener bodegas de configuración (sistema manual)
        bodegas_config = db.get_bodegas()
        print(f"DEBUG: Bodegas de configuración: {len(bodegas_config)}")
        for b in bodegas_config:
            print(f"  - {b.nombre} (supplier_id: {b.supplier_id})")
        
        # Obtener bodegas marcadas en seguimiento (sistema automático)
        warehouses_seguimiento = db.get_warehouses_seguimiento()
        print(f"DEBUG: Bodegas en seguimiento: {len(warehouses_seguimiento)}")
        for wh in warehouses_seguimiento:
            print(f"  - {wh.get('warehouse_name')} (supplier_id: {wh.get('supplier_id')})")
        
        # Verificar que haya al menos una bodega para descargar
        if not bodegas_config and not warehouses_seguimiento:
            messagebox.showwarning(
                "Sin bodegas", 
                "Agrega bodegas en ⚙ Configuración o marca bodegas en seguimiento en 🏪 Bodegas.", 
                parent=self
            )
            return

        # Adaptar formato de datos para compatibilidad con _process_bodega
        class BodegaAdapter:
            def __init__(self, bodega_dict, from_config=True):
                if from_config:
                    # Bodegas de configuración (tabla bodegas)
                    self.supplier_id = bodega_dict.supplier_id
                    self.productos_esperados = bodega_dict.productos_esperados
                    self.nombre = bodega_dict.nombre
                    self.id = bodega_dict.id
                else:
                    # Bodegas de seguimiento (tabla warehouses)
                    self.supplier_id = bodega_dict.get('supplier_id')
                    self.productos_esperados = bodega_dict.get('productos_count', 0)
                    self.nombre = bodega_dict.get('warehouse_name', '')
                    self.id = bodega_dict.get('warehouse_id')

        # Primero bodegas de configuración, luego bodegas de seguimiento
        bodegas = [BodegaAdapter(b, from_config=True) for b in bodegas_config]
        bodegas.extend([BodegaAdapter(wh, from_config=False) for wh in warehouses_seguimiento])
        
        print(f"DEBUG: Total bodegas a descargar: {len(bodegas)}")
        for i, b in enumerate(bodegas):
            print(f"  {i+1}. {b.nombre} (supplier_id: {b.supplier_id})")

        # Reset stop event antes de iniciar
        self._stop_event.clear()
        self._run_in_thread(lambda: self._download_all_worker(email, password, bodegas))

    def _get_fresh_token(self, email: str, password: str) -> str:
        """Hace login y devuelve un token fresco. Cachea en settings."""
        self._log("🔐 Iniciando sesión en Dropi…")
        token = login_dropi(email, password)
        cfg.save_token(token)
        self._log("✓ Sesión iniciada. Token obtenido.")
        return token

    def _process_bodega(self, bodega, token, email, password):
        """
        Procesa una bodega individual (para uso con ThreadPoolExecutor).
        Retorna un diccionario con resultados.
        """
        # Crear un callback thread-safe para logging
        log_queue = queue.Queue()
        
        def log_callback(msg):
            log_queue.put(msg)
        
        result = {
            'bodega': bodega,
            'productos': [],
            'error': None,
            'logs': []
        }
        
        try:
            productos = descargar_bodega(
                token, bodega.supplier_id,
                callback=log_callback,
                stop_event=self._stop_event,
                productos_esperados=bodega.productos_esperados,
            )
            result['productos'] = productos
            result['logs'] = list(log_queue.queue)
            
        except PermissionError:
            # Token expiró — intentar renovar una vez
            try:
                new_token = self._get_fresh_token(email, password)
                productos = descargar_bodega(
                    new_token, bodega.supplier_id,
                    callback=log_callback,
                    stop_event=self._stop_event,
                    productos_esperados=bodega.productos_esperados,
                )
                result['productos'] = productos
                result['logs'] = list(log_queue.queue)
            except Exception as exc:
                result['error'] = str(exc)
                result['logs'] = list(log_queue.queue)
                
        except Exception as exc:
            result['error'] = str(exc)
            result['logs'] = list(log_queue.queue)
        
        return result

    def _download_all_worker(self, email: str, password: str, bodegas: list) -> None:
        self._running_download = True
        self._ui_queue.put(lambda: self._btn_download.configure(state="disabled", text="⏳ Descargando…"))
        self._ui_queue.put(lambda: self._btn_stop.configure(state="normal", text="⛔  Detener"))
        self._ui_queue.put(lambda: self._progress_bar.start())
        self._ui_queue.put(lambda: self._set_status("⏳ Descargando…", C_WARNING))

        # Auto-login para obtener token fresco
        try:
            token = self._get_fresh_token(email, password)
        except Exception as exc:
            self._log(f"🔴 Error al iniciar sesión: {exc}")
            self._running_download = False
            self._ui_queue.put(lambda: self._btn_download.configure(state="normal", text="⬇  Descargar ahora"))
            self._ui_queue.put(lambda: self._btn_stop.configure(state="disabled", text="⛔  Detener"))
            self._ui_queue.put(lambda: self._progress_bar.stop())
            self._ui_queue.put(lambda: self._set_status("● Listo", C_SUCCESS))
            return

        total_products = 0
        errores_global = []
        cancelled = False

        self._log("─" * 60)
        self._log(f"🚀 Iniciando descarga paralela — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        self._log(f"   Bodegas: {len(bodegas)}")
        self._log(f"   Workers paralelos: 2")
        self._log("─" * 60)

        # Usar ThreadPoolExecutor para procesamiento paralelo
        MAX_WORKERS = 2  # Número de bodegas simultáneas (reducido para mejor precisión)
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Crear futures para todas las bodegas
            future_to_bodega = {
                executor.submit(self._process_bodega, bodega, token, email, password): bodega
                for bodega in bodegas
            }
            
            # Procesar resultados a medida que completan
            for future in as_completed(future_to_bodega):
                if self._stop_event.is_set():
                    cancelled = True
                    # Cancelar futures pendientes
                    for f in future_to_bodega:
                        f.cancel()
                    break
                
                bodega = future_to_bodega[future]
                try:
                    result = future.result()
                    
                    # Mostrar logs de la bodega
                    for log_msg in result['logs']:
                        self._log(log_msg)
                    
                    if result['error']:
                        self._log(f"  🔴 Error en {bodega.nombre}: {result['error']}")
                        errores_global.append(f"{bodega.nombre}: {result['error']}")
                    else:
                        productos = result['productos']
                        if productos:
                            db.save_productos_lote(productos)
                            n_products = len(productos)
                            total_products += n_products
                            self._log(f"  ✅ Guardados {n_products} productos de {bodega.nombre}")
                        else:
                            self._log(f"  ⚠ No se descargaron productos para {bodega.nombre}")
                    
                    # Log actividad
                    log = LogActividad(
                        fecha=datetime.now().strftime("%Y-%m-%d"),
                        hora=datetime.now().strftime("%H:%M:%S"),
                        bodega=bodega.nombre,
                        productos_descargados=len(result['productos']),
                        errores=result['error'] or "",
                    )
                    db.insertar_log(log)
                    
                except Exception as exc:
                    self._log(f"  🔴 Error procesando {bodega.nombre}: {exc}")
                    errores_global.append(f"{bodega.nombre}: {exc}")

        self._log("\n" + "─" * 60)
        if cancelled:
            self._log(f"⛔ Descarga cancelada. Productos descargados hasta ahora: {total_products}")
        else:
            self._log(f"✅ Descarga finalizada.  Total productos: {total_products}")
        if errores_global:
            self._log(f"⚠ Errores: {len(errores_global)}")
        self._log("─" * 60 + "\n")

        self._running_download = False
        self._stop_event.clear()
        self._ui_queue.put(lambda: self._btn_download.configure(state="normal", text="⬇  Descargar ahora"))
        self._ui_queue.put(lambda: self._btn_stop.configure(state="disabled", text="⛔  Detener"))
        self._ui_queue.put(lambda: self._progress_bar.stop())
        self._ui_queue.put(lambda: self._progress_bar.set(0))
        self._ui_queue.put(lambda: self._set_status("● Listo", C_SUCCESS))
        self._ui_queue.put(lambda: self._refresh_historial_tab())
        self._ui_queue.put(lambda: self._refresh_dashboard())

    def _agregar_bodega(self) -> None:
        dlg = BodegaDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            ok = db.add_bodega(dlg.result)
            if ok:
                self._log(f"✓ Bodega agregada: {dlg.result.nombre} (supplier_id={dlg.result.supplier_id})")
                self._refresh_bodega_tree()
                self._refresh_bodega_combo()
            else:
                messagebox.showerror(
                    "Error",
                    f"Ya existe una bodega con supplier_id={dlg.result.supplier_id}.",
                    parent=self,
                )

    def _importar_bodegas_excel(self) -> None:
        """Importa bodegas desde un archivo Excel."""
        file_path = filedialog.askopenfilename(
            title="Seleccionar archivo Excel de bodegas",
            filetypes=[("Archivos Excel", "*.xlsx *.xls"), ("Todos los archivos", "*.*")]
        )
        
        if not file_path:
            return
        
        self._log("📥 Importando bodegas desde Excel...")
        
        exitosas, fallidas, errores = db.importar_bodegas_excel(file_path)
        
        self._log(f"✅ Importación completada:")
        self._log(f"   Exitosas: {exitosas}")
        self._log(f"   Fallidas: {fallidas}")
        
        if errores:
            self._log(f"⚠ Errores:")
            for error in errores[:10]:  # Mostrar solo primeros 10 errores
                self._log(f"   - {error}")
            if len(errores) > 10:
                self._log(f"   ... y {len(errores) - 10} errores más")
        
        if exitosas > 0:
            self._refresh_bodega_tree()
            self._refresh_bodega_combo()
            messagebox.showinfo(
                "Importación completada",
                f"Bodegas importadas exitosamente: {exitosas}\nFallidas: {fallidas}",
                parent=self
            )
        else:
            messagebox.showerror(
                "Error en importación",
                f"No se pudo importar ninguna bodega.\nErrores: {errores[0] if errores else 'Desconocido'}",
                parent=self
            )

    def _editar_bodega(self) -> None:
        sel = self._bodega_tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecciona una bodega para editar.", parent=self)
            return
        bid = int(sel[0])
        bodegas = {b.id: b for b in db.get_bodegas()}
        bodega = bodegas.get(bid)
        if not bodega:
            return

        dlg = BodegaDialog(self, bodega)
        self.wait_window(dlg)
        if dlg.result:
            dlg.result.id = bid
            db.update_bodega(dlg.result)
            self._log(f"✓ Bodega actualizada: {dlg.result.nombre}")
            self._refresh_bodega_tree()
            self._refresh_bodega_combo()

    def _eliminar_bodega(self) -> None:
        sel = self._bodega_tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecciona una bodega para eliminar.", parent=self)
            return
        bid = int(sel[0])
        nombre = self._bodega_tree.item(sel[0])["values"][0]
        if messagebox.askyesno(
            "Confirmar", f"¿Eliminar la bodega '{nombre}'?\nSe conservarán los datos históricos.",
            parent=self
        ):
            db.delete_bodega(bid)
            self._log(f"🗑 Bodega eliminada: {nombre}")
            self._refresh_bodega_tree()
            self._refresh_bodega_combo()
    def _export_all(self) -> None:
        self._run_in_thread(self._export_all_worker)

    def _export_all_worker(self) -> None:
        try:
            self._log("📤 Exportando reporte completo…")
            path = exporter.exportar_completo()
            self._log(f"✅ Excel guardado en: {path}")
            self._ui_queue.put(lambda: os.startfile(path))
        except Exception as exc:
            self._log(f"🔴 Error al exportar: {exc}")

    # ──────────────────────────────────────────────────────────────────────────
    #  ACCIONES: DASHBOARD
    # ──────────────────────────────────────────────────────────────────────────

    def _refresh_bodega_tree(self) -> None:
        for item in self._bodega_tree.get_children():
            self._bodega_tree.delete(item)
        for b in db.get_bodegas():
            self._bodega_tree.insert(
                "", "end",
                iid=str(b.id),
                values=(b.nombre, b.url, b.supplier_id),
            )

    def _refresh_bodega_combo(self) -> None:
        bodegas = db.get_bodegas()
        # Obtener stock total por bodega
        names_with_stock = []
        for b in bodegas:
            productos = db.get_productos_bodega(b.supplier_id)
            stock_total = sum(p.get("stock", 0) for p in productos)
            names_with_stock.append(f"{b.nombre} ({stock_total:,})")
        
        self._bodega_combo.configure(values=names_with_stock)
        if names_with_stock:
            self._bodega_combo.set(names_with_stock[0])
            self._refresh_dashboard()
        else:
            self._bodega_combo.set("")

    def _on_bodega_select(self, value: str = "") -> None:
        if hasattr(self, "_search_entry"):
            self._search_entry.delete(0, "end")
            self._dashboard_table.set_search_query("")
        if hasattr(self, "_filter_combo"):
            self._filter_combo.set("Todos")
            self._dashboard_table.set_filter_type("Todos")
        self._refresh_dashboard()

    def _on_filter_change(self, value: str) -> None:
        self._dashboard_table.set_filter_type(value)

    def _on_search_change(self, _event=None) -> None:
        query = self._search_entry.get()
        self._dashboard_table.set_search_query(query)

    def _refresh_dashboard(self) -> None:
        nombre_con_stock = self._bodega_combo.get()
        if not nombre_con_stock:
            return

        # Extraer nombre limpio (eliminar el stock entre paréntesis)
        match = re.match(r"^(.+?)\s*\(\d+[,\d]*\)$", nombre_con_stock)
        if match:
            nombre = match.group(1).strip()
        else:
            nombre = nombre_con_stock

        bodegas = {b.nombre: b for b in db.get_bodegas()}
        bodega = bodegas.get(nombre)
        if not bodega:
            return

        # Usar paginación para cargar productos en lotes
        PAGE_SIZE = 100
        offset = 0
        all_productos = []
        
        # Obtener total de productos primero
        total_productos = db.count_productos_bodega(bodega.supplier_id)
        
        # Cargar productos en lotes
        while offset < total_productos:
            productos_lote = db.get_productos_bodega_paginado(bodega.supplier_id, offset, PAGE_SIZE)
            all_productos.extend(productos_lote)
            offset += PAGE_SIZE
            
            # Actualizar progreso en UI
            progress = min(offset / total_productos * 100, 100)
            self._ui_queue.put(lambda p=progress: self._progress_bar.set(p))
            
            # Si no hay más productos, salir
            if len(productos_lote) < PAGE_SIZE:
                break
        
        # Obtener período seleccionado
        period_map = {
            "Todo el historial": None,
            "Últimas 24h": 24,
            "Últimas 48h": 48,
            "Últimas 72h": 72,
            "Última semana": 168,
            "Últimos 30 días": 720,
        }
        horas = period_map.get(self._inventory_period.get(), None)
        
        # Obtener stock anterior según período
        stock_anterior = db.get_stock_por_periodo(bodega.supplier_id, horas)
        
        # Calcular cambios para cada producto
        for prod in all_productos:
            stock_actual = prod.get("stock", 0)
            stock_ant = stock_anterior.get(prod["product_id_dropi"], stock_actual)
            prod["stock_anterior"] = stock_ant
            prod["cambio"] = stock_actual - stock_ant
        
        # Aplicar ordenamiento
        sort_option = self._inventory_sort.get()
        if sort_option == "Cambios (desc)":
            all_productos.sort(key=lambda x: abs(x["cambio"]), reverse=True)
        elif sort_option == "Cambios (asc)":
            all_productos.sort(key=lambda x: abs(x["cambio"]))
        elif sort_option == "Nombre (A-Z)":
            all_productos.sort(key=lambda x: x.get("nombre", "").lower())
        elif sort_option == "Nombre (Z-A)":
            all_productos.sort(key=lambda x: x.get("nombre", "").lower(), reverse=True)
        elif sort_option == "Stock (desc)":
            all_productos.sort(key=lambda x: x.get("stock", 0), reverse=True)
        elif sort_option == "Stock (asc)":
            all_productos.sort(key=lambda x: x.get("stock", 0))
        
        self._dashboard_table.refresh(all_productos, stock_anterior)

        # Stats
        total = len(all_productos)
        bajos = sum(
            1 for p in all_productos
            if stock_anterior.get(p["product_id_dropi"]) is not None
            and p["stock"] < stock_anterior[p["product_id_dropi"]]
        )
        subieron = sum(
            1 for p in all_productos
            if stock_anterior.get(p["product_id_dropi"]) is not None
            and p["stock"] > stock_anterior[p["product_id_dropi"]]
        )
        self._stat_total.configure(text=str(total))
        self._stat_bajos.configure(text=str(bajos))
        self._stat_subieron.configure(text=str(subieron))

    def _open_product_detail(self, producto: dict) -> None:
        ProductDetailWindow(self, producto, modo_ventas=True)

    def _open_hot_product_detail(self, producto: dict) -> None:
        ProductDetailWindow(self, producto, modo_ventas=True)

    def _refresh_hot(self) -> None:
        period_map = {
            "Últimas 24h": 24,
            "Últimas 48h": 48,
            "Últimas 72h": 72,
            "Última semana": 168,
            "Últimos 30 días": 720,
            "Todo el historial": None,
        }
        horas = period_map.get(self._hot_period.get(), 24)
        data = db.get_productos_calientes(horas)
        
        # Aplicar ordenamiento
        sort_option = self._hot_sort.get()
        if sort_option == "Ventas (desc)":
            data.sort(key=lambda x: x.get("ventas_estimadas", 0), reverse=True)
        elif sort_option == "Ventas (asc)":
            data.sort(key=lambda x: x.get("ventas_estimadas", 0))
        elif sort_option == "Nombre (A-Z)":
            data.sort(key=lambda x: x.get("nombre", "").lower())
        elif sort_option == "Nombre (Z-A)":
            data.sort(key=lambda x: x.get("nombre", "").lower(), reverse=True)
        elif sort_option == "Bodega (A-Z)":
            data.sort(key=lambda x: x.get("bodega_nombre", "").lower())
        
        self._hot_table.refresh(data)

    def _nombre_bodega_limpia(self) -> str:
        nombre_con_stock = self._bodega_combo.get() if hasattr(self, "_bodega_combo") else ""
        match = re.match(r"^(.+?)\s*\(\d+[,\d]*\)$", nombre_con_stock or "")
        if match:
            return match.group(1).strip()
        return (nombre_con_stock or "").strip()

    def _export_inventario(self) -> None:
        nombre = self._nombre_bodega_limpia()
        bodegas = {b.nombre: b for b in db.get_bodegas()}
        sid = bodegas[nombre].supplier_id if nombre in bodegas else None

        period_map = {
            "Todo el historial": None,
            "Últimas 24h": 24,
            "Últimas 48h": 48,
            "Últimas 72h": 72,
            "Última semana": 168,
            "Últimos 30 días": 720,
        }
        horas = period_map.get(self._inventory_period.get(), None)
        filtro = self._filter_combo.get() if hasattr(self, "_filter_combo") else "Todos"
        busqueda = self._search_entry.get() if hasattr(self, "_search_entry") else ""
        orden = self._inventory_sort.get() if hasattr(self, "_inventory_sort") else "Cambios (desc)"

        def _worker():
            try:
                self._log("📤 Exportando inventario (imagen, stock y ventas)…")
                path = exporter.exportar_inventario(
                    sid,
                    horas=horas,
                    filtro=filtro,
                    busqueda=busqueda,
                    orden=orden,
                    progress_cb=self._log,
                )
                self._log(f"✅ Excel guardado: {path}")
                self._ui_queue.put(lambda: os.startfile(path))
            except Exception as exc:
                self._log(f"🔴 Error: {exc}")

        self._run_in_thread(_worker)

    def _export_calientes(self) -> None:
        try:
            period_map = {
                "Últimas 24h": 24, "Últimas 48h": 48,
                "Últimas 72h": 72, "Última semana": 168,
                "Últimos 30 días": 720,
                "Todo el historial": None,
            }
            horas = period_map.get(self._hot_period.get(), 24)
            self._log("📤 Exportando productos calientes (imagen, stock y ventas)…")
            path = exporter.exportar_calientes(horas, progress_cb=self._log)
            self._log(f"✅ Excel guardado: {path}")
            self._ui_queue.put(lambda: os.startfile(path))
        except Exception as exc:
            self._log(f"🔴 Error: {exc}")

    def _export_reporte_historico(self) -> None:
        try:
            self._log("📊 Generando reporte histórico de productos…")
            path = exporter.exportar_reporte_historico()
            self._log(f"✅ Reporte histórico guardado: {path}")
            self._ui_queue.put(lambda: os.startfile(path))
        except Exception as exc:
            self._log(f"🔴 Error: {exc}")

    # ──────────────────────────────────────────────────────────────────────────
    #  ACCIONES: HISTORIAL TAB
    # ──────────────────────────────────────────────────────────────────────────

    def _refresh_historial_tab(self) -> None:
        for item in self._log_tree.get_children():
            self._log_tree.delete(item)
        for row in db.get_log_actividad():
            self._log_tree.insert(
                "", "end",
                values=(
                    row["fecha"],
                    row["hora"],
                    row["bodega"],
                    row["productos_descargados"],
                    row["errores"] or "—",
                ),
            )

    # ──────────────────────────────────────────────────────────────────────────
    #  SCHEDULER TRIGGER
    # ──────────────────────────────────────────────────────────────────────────

    def _on_scheduler_trigger(self) -> None:
        hora = datetime.now().strftime("%H:%M")
        self._log(f"\n⏰ Descarga automática programada ({hora})")
        self._start_download()

    # ──────────────────────────────────────────────────────────────────────────
    #  UTILIDADES
    # ──────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        """Thread-safe: agrega una línea al log de descargas."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self._ui_queue.put(lambda m=line: self._append_log(m))

    def _append_log(self, line: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _set_status(self, text: str, color: str) -> None:
        self._status_label.configure(text=text, text_color=color)

    def _run_in_thread(self, fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def _poll_queue(self) -> None:
        """Procesa tareas de la cola de UI (llamado desde el main thread cada 80ms)."""
        try:
            while True:
                item = self._ui_queue.get_nowait()
                if isinstance(item, tuple):
                    task_type, data = item
                    if task_type == "log":
                        self._log(data)
                    elif task_type == "error":
                        self._log(f"🔴 Error: {data}")
                        messagebox.showerror("Error", data)
                    elif task_type == "status":
                        self._set_status(f"● {data}", C_SUCCESS)
                    elif task_type == "sync_complete":
                        nuevos_sup, actualizados_sup, saltados_sup, nuevas_wh, actualizadas_wh = data
                        msg = (f"✅ Sincronización completada:\n"
                               f"   Proveedores: {nuevos_sup} nuevos, {actualizados_sup} actualizados, {saltados_sup} ya existentes\n"
                               f"   Bodegas: {nuevas_wh} nuevas, {actualizadas_wh} actualizadas")
                        self._log(msg)
                        messagebox.showinfo("Sincronización completada", msg)
                        self._refresh_bodegas_tab()
                    elif task_type == "ordenes_complete":
                        exitosas, fallidas = data
                        msg = (f"✅ Extracción de órdenes completada:\n"
                               f"   Exitosas: {exitosas}\n"
                               f"   Fallidas: {fallidas}")
                        self._log(msg)
                        messagebox.showinfo("Extracción completada", msg)
                        self._refresh_bodegas_tab()
                    elif task_type == "refresh_bodegas":
                        self._refresh_bodegas_tab()
                    else:
                        item()
                else:
                    item()
        except queue.Empty:
            pass
        finally:
            self.after(80, self._poll_queue)

    # ──────────────────────────────────────────────────────────────────────────
    #  TAB 5: TODO LO VENDIDO (ELIMINADO)
    # ──────────────────────────────────────────────────────────────────────────
    # Se eliminó debido a errores de memoria al cargar miles de productos.
    # 
    # ALTERNATIVAS DISPONIBLES:
    # 1. Exportar a Excel los productos con movimientos (función get_productos_con_movimientos en database.py)
    # 2. Usar la sección "Más Vendidos" en Dashboard que muestra los productos con más cambios
    # 3. Agregar un botón de exportación en la sección Historial para descargar productos con movimientos
    # 4. Implementar paginación real en el futuro (cargar solo productos visibles en pantalla)

    def _on_close(self) -> None:
        self._scheduler.shutdown()
        try:
            db.checkpoint_db()
        except Exception:
            pass
        self.destroy()


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.chdir(APP_DIR)
    app = DropiTrackerApp()
    app.mainloop()
