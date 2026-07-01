from pathlib import Path
import shutil
import json
import sys
import math
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ir_sender import IRSendError, learn_ir_command, list_serial_ports, send_ir_command, test_arduino_connection
from remote_config import RemoteConfigManager
from remote_devices import BUILTIN_DEVICE_LIBRARY, DeviceDefinition

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

try:
    import pystray
except Exception:
    pystray = None

try:
    from pynput import keyboard as pynput_keyboard  # type: ignore[import-not-found]
except Exception:
    pynput_keyboard = None


PROGRAM_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = PROGRAM_DIR / "conf" / "remote_settings.ini"
APP_VERSION_FILE = PROGRAM_DIR / "conf" / "app_version.txt"
IMAGE_DIR = PROGRAM_DIR / "images"
BASE_SKIN_OPTIONS = ["Classic", "Slate"]


class IRRemoteApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("IR Remote Sender")
        self.root.geometry("1000x700")
        self.root.minsize(760, 560)
        self.tray_icon = None
        self.app_version = self._read_app_version()

        self.config_manager = RemoteConfigManager(CONFIG_FILE)

        self.show_overlays_var = tk.BooleanVar(value=self.config_manager.get_show_overlays())
        self.overlay_radio_var = tk.StringVar(value="on" if self.show_overlays_var.get() else "off")
        self.status_var = tk.StringVar(value="Ready")
        self.connection_var = tk.StringVar(value="Connection: Not tested")
        saved_port = self.config_manager.get_serial_port()
        self.serial_port_var = tk.StringVar(value=saved_port if saved_port else "Auto")

        self.hotkey_vars: dict[str, dict[str, tk.StringVar]] = {}
        self.device_skin_vars: dict[str, tk.StringVar] = {}
        self.bindings: dict[tuple[str, str], str] = {}
        self.global_hotkey_handles: dict[tuple[str, str], object] = {}
        self.global_hotkey_listener = None
        self.hotkey_toggle_state: dict[str, int] = {}
        self.device_containers: dict[str, tk.Frame] = {}
        self.last_selected_device: str | None = None

        self.device_library: dict[str, DeviceDefinition] = {}
        self.refresh_device_library()
        self.enabled_devices: list[str] = [name for name in self.config_manager.get_enabled_devices() if name in self.device_library]

        self.create_ui()
        self.apply_hotkeys()
        self.set_connection_state(False, "Not tested")
        
        # Setup window close to minimize to tray
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        
        # Start minimized
        self.root.withdraw()
        self.root.after(100, self.setup_tray)
        
        # Auto-connect on startup
        self.root.after(200, self.auto_connect_on_startup)

    def refresh_device_library(self):
        self.device_library = dict(BUILTIN_DEVICE_LIBRARY)
        for name, data in self.config_manager.get_custom_devices().items():
            self.device_library[name] = DeviceDefinition(
                name=name,
                commands=dict(data["commands"]),
                image_file=data.get("image_file"),
            )

    def _read_app_version(self) -> str:
        default_version = "v2.0"
        try:
            if not APP_VERSION_FILE.exists():
                APP_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                APP_VERSION_FILE.write_text(default_version + "\n", encoding="utf-8")
                return default_version

            version_text = APP_VERSION_FILE.read_text(encoding="utf-8").strip()
            if version_text:
                return version_text
        except Exception:
            pass
        return default_version

    def _store_custom_image(self, source: str) -> str | None:
        src_path = Path(source)
        if not source or not src_path.exists():
            return None

        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        target_path = IMAGE_DIR / src_path.name
        try:
            if src_path.resolve() != target_path.resolve():
                shutil.copy2(src_path, target_path)
        except Exception as exc:
            messagebox.showerror("Image copy failed", f"Unable to copy image into images folder.\n{exc}")
            return None

        return target_path.name

    def _normalize_custom_image_value(self, image_value: str) -> str | None:
        image_value = image_value.strip()
        if not image_value:
            return None

        image_path = Path(image_value)
        existing_image = IMAGE_DIR / image_path.name
        if existing_image.exists() and image_value == image_path.name:
            return image_path.name

        stored_name = self._store_custom_image(image_value)
        if stored_name:
            return stored_name

        if image_path.is_absolute() or image_path.parent != Path("."):
            messagebox.showwarning("Image not found", "The selected custom image could not be found.")
            return None

        return image_path.name

    def create_ui(self):
        ttk.Style(self.root).theme_use("clam")
        self.create_menu_bar()

        main = ttk.Frame(self.root, padding=(12, 8, 12, 12))
        main.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        self.rebuild_device_tabs()

        status_bar = ttk.Frame(main, padding=(0, 8, 0, 0))
        status_bar.pack(fill="x")
        ttk.Label(status_bar, textvariable=self.status_var, foreground="#2563eb").pack(side="left", anchor="w")
        self.connection_label = ttk.Label(status_bar, textvariable=self.connection_var, foreground="#9a3412")
        self.connection_label.pack(side="right", anchor="e")

    def set_connection_state(self, connected: bool, detail: str = ""):
        if connected:
            message = f"Connection: OK ({detail})" if detail else "Connection: OK"
            color = "#15803d"
        else:
            message = f"Connection: {detail}" if detail else "Connection: Offline"
            color = "#9a3412"
        self.connection_var.set(message)
        if hasattr(self, "connection_label"):
            self.connection_label.configure(foreground=color)

    def _selected_notebook_device_name(self) -> str | None:
        if not hasattr(self, "notebook"):
            return None

        current_tab = self.notebook.select()
        if not current_tab:
            return None

        try:
            device_name = self.notebook.tab(current_tab, "text")
        except tk.TclError:
            return None

        return device_name or None

    def _on_notebook_tab_changed(self, _event=None):
        selected_device = self._selected_notebook_device_name()
        if selected_device:
            self.last_selected_device = selected_device

    def _center_window(self, window: tk.Misc, parent: tk.Misc | None = None):
        anchor = parent if parent is not None else self.root
        try:
            window.update_idletasks()
            if anchor is not None:
                anchor.update_idletasks()

            win_w = window.winfo_width() or window.winfo_reqwidth()
            win_h = window.winfo_height() or window.winfo_reqheight()

            if anchor is not None and anchor.winfo_ismapped():
                anchor_x = anchor.winfo_rootx()
                anchor_y = anchor.winfo_rooty()
                anchor_w = anchor.winfo_width()
                anchor_h = anchor.winfo_height()
            else:
                anchor_x = 0
                anchor_y = 0
                anchor_w = window.winfo_screenwidth()
                anchor_h = window.winfo_screenheight()

            x = max(0, int(anchor_x + (anchor_w - win_w) / 2))
            y = max(0, int(anchor_y + (anchor_h - win_h) / 2))
            window.geometry(f"+{x}+{y}")

            # Show only after position is finalized to avoid top-left flicker.
            try:
                if not window.winfo_viewable():
                    window.deiconify()
            except Exception:
                pass

            try:
                window.lift()
                window.focus_force()
                window.grab_set()
            except Exception:
                pass
        except Exception:
            pass

    def _prepare_dialog(self, window: tk.Misc, parent: tk.Misc | None = None):
        anchor = parent if parent is not None else self.root
        try:
            window.transient(anchor)
        except Exception:
            pass
        try:
            # Keep hidden until _center_window computes final position.
            window.withdraw()
        except Exception:
            pass

    def create_menu_bar(self):
        menubar = tk.Menu(self.root)
        self.root.configure(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Import Device Profile", command=self.import_device_profile)
        file_menu.add_command(label="Export Device Profile", command=self.open_export_device_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Hide", command=self.minimize_to_tray)
        file_menu.add_command(label="Quit", command=self.actual_quit_app)

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Device Setup", command=self.open_settings_dialog)
        settings_menu.add_command(label="Add Device Pane", command=self.open_add_device_dialog)
        settings_menu.add_command(label="Remove Device Pane", command=self.open_remove_device_dialog)
        settings_menu.add_command(label="Edit Device Commands", command=self.open_edit_device_commands_dialog)
        settings_menu.add_separator()
        self.settings_menu = settings_menu
        self.overlay_menu_index = settings_menu.index("end") + 1
        settings_menu.add_command(label=self._overlay_toggle_label(), command=self.toggle_show_overlays_button)

        menubar.add_command(label="About", command=self.open_about_dialog)

    def _overlay_toggle_label(self) -> str:
        return "Hide Overlays" if self.show_overlays_var.get() else "Show Overlays"

    def _sync_overlay_menu_label(self):
        if hasattr(self, "settings_menu") and self.settings_menu is not None and hasattr(self, "overlay_menu_index"):
            self.settings_menu.entryconfig(self.overlay_menu_index, label=self._overlay_toggle_label())

    def _save_config_nonfatal(self, reason: str = "") -> bool:
        """Save config without breaking UI callbacks when disk writes fail."""
        try:
            self.config_manager.save()
            return True
        except Exception as exc:
            context = f" ({reason})" if reason else ""
            self.status_var.set(f"Config save failed{context}: {exc}")
            return False

    def on_overlay_toggle_from_main(self):
        value = self.overlay_radio_var.get() == "on"
        self.show_overlays_var.set(value)
        self.config_manager.set_show_overlays(value)
        self._save_config_nonfatal("overlay toggle")
        self._sync_overlay_menu_label()
        self.rebuild_device_tabs()

    def on_overlay_toggle_from_menu(self):
        value = bool(self.show_overlays_var.get())
        self.overlay_radio_var.set("on" if value else "off")
        self.config_manager.set_show_overlays(value)
        self._save_config_nonfatal("overlay toggle")
        self._sync_overlay_menu_label()
        self.rebuild_device_tabs()

    def toggle_show_overlays_button(self):
        """Toggle overlays and rebuild device tabs."""
        current_state = self.show_overlays_var.get()
        self.show_overlays_var.set(not current_state)
        self.overlay_radio_var.set("off" if current_state else "on")
        self.config_manager.set_show_overlays(not current_state)
        self._save_config_nonfatal("overlay toggle")
        self._sync_overlay_menu_label()
        self.rebuild_device_tabs()

    def rebuild_device_tabs(self):
        selected_device_name = None
        selected_device_name = self._selected_notebook_device_name()

        for tab_id in self.notebook.tabs():
            self.notebook.forget(tab_id)
        self.device_containers.clear()

        if not self.enabled_devices:
            empty_tab = ttk.Frame(self.notebook, padding=14)
            self.notebook.add(empty_tab, text="No Devices")
            ttk.Label(
                empty_tab,
                text="No devices configured. Open Settings -> Add Device Pane.",
                foreground="#4b5563",
            ).pack(anchor="w")
            ttk.Button(empty_tab, text="Add Device", command=self.open_add_device_dialog).pack(anchor="w", pady=(10, 0))
            return

        for device_name in self.enabled_devices:
            if device_name in self.device_library:
                self.create_remote_tab(device_name)

        if selected_device_name:
            self.last_selected_device = selected_device_name
            for tab_id in self.notebook.tabs():
                if self.notebook.tab(tab_id, "text") == selected_device_name:
                    self.notebook.select(tab_id)
                    break

    def _device_skin(self, device_name: str) -> str:
        device = self.device_library[device_name]
        options = self._skin_options_for_device(device_name)
        skin = self.config_manager.get_device_skin(device_name)

        # Backward compatibility with older logical image skin names.
        if device.image_file and skin == "RTC-850" and device.image_file == "RTC-850.png":
            skin = device.image_file

        if device.image_file and skin == device.image_file:
            skin = "Custom Image"

        if skin not in options:
            skin = "Classic"
        return skin

    def _skin_options_for_device(self, device_name: str) -> list[str]:
        device = self.device_library[device_name]
        options = list(BASE_SKIN_OPTIONS)
        if device.image_file:
            options.append("Custom Image")
        return options

    def create_remote_tab(self, device_name: str):
        device = self.device_library[device_name]
        skin = self._device_skin(device_name)

        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text=device_name)

        dark = skin in {"Slate"} or (device.image_file is not None and skin == "Custom Image")
        panel_bg = "#111827" if dark else "#ffffff"
        fg = "#f9fafb" if dark else "#111827"

        remote_panel = tk.Frame(tab, bg=panel_bg, padx=16, pady=16, bd=2, relief="groove")
        remote_panel.pack(fill="both", expand=True)

        tk.Label(remote_panel, text=f"{device_name} Remote", bg=panel_bg, fg=fg, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 10))

        container = tk.Frame(remote_panel, bg=panel_bg)
        container.pack(fill="both", expand=True)
        self.device_containers[device_name] = container

        if self._should_use_image_skin(device_name, skin):
            self._build_image_panel(container, device_name)
        else:
            self._build_button_panel(container, device_name, dark)

        controls = tk.Frame(remote_panel, bg=panel_bg)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="Hide Overlays" if self.show_overlays_var.get() else "Show Overlays", command=self.toggle_show_overlays_button).pack(side="left")
        if device.image_file:
            ttk.Button(controls, text="Tune Overlays", command=lambda d=device_name: self.open_overlay_tuner(d)).pack(side="left", padx=(8, 0))
            ttk.Button(controls, text="Clear Overlays", command=lambda d=device_name: self.clear_device_overlays(d)).pack(side="left", padx=(8, 0))

    def _should_use_image_skin(self, device_name: str, skin: str) -> bool:
        device = self.device_library[device_name]
        if skin in {"Classic", "Slate"}:
            return False
        return bool(device.image_file and skin == "Custom Image" and Image is not None and ImageTk is not None)

    def _build_button_panel(self, container: tk.Frame, device_name: str, dark: bool):
        device = self.device_library[device_name]
        button_grid = tk.Frame(container, bg=container.cget("bg"))
        button_grid.pack(fill="x")

        bg = "#374151" if dark else "#e5e7eb"
        fg = "#f9fafb" if dark else "#111827"
        abg = "#4b5563" if dark else "#d1d5db"

        actions = list(device.commands.keys())
        columns = 2
        for idx, action_name in enumerate(actions):
            row = idx // columns
            col = idx % columns
            btn = tk.Button(
                button_grid,
                text=action_name,
                width=18,
                height=2,
                bg=bg,
                fg=fg,
                activebackground=abg,
                activeforeground=fg,
                relief="flat",
                bd=0,
                cursor="hand2",
                command=lambda d=device_name, a=action_name: self.send_command(d, a),
            )
            btn.grid(row=row, column=col, padx=6, pady=6, sticky="ew")
            button_grid.grid_columnconfigure(col, weight=1)

    def _build_image_panel(self, container: tk.Frame, device_name: str):
        device = self.device_library[device_name]
        img_path = PROGRAM_DIR / "images" / str(device.image_file)
        if not img_path.exists():
            self._build_button_panel(container, device_name, dark=True)
            return

        pil_image = Image.open(img_path).convert("RGBA")
        regions = self._regions_for_device(device_name)

        canvas = tk.Canvas(container, bg=container.cget("bg"), highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas._photo = None
        canvas._image_area = (0, 0, 0, 0)

        def redraw(_event=None):
            cw = canvas.winfo_width()
            ch = canvas.winfo_height()
            if cw <= 1 or ch <= 1:
                return

            iw, ih = pil_image.size
            scale = min(cw / iw, ch / ih)
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))

            resized = pil_image.resize((nw, nh), Image.LANCZOS)
            photo = ImageTk.PhotoImage(resized)
            canvas._photo = photo

            canvas.delete("all")
            ox = (cw - nw) // 2
            oy = (ch - nh) // 2
            canvas._image_area = (ox, oy, nw, nh)

            canvas.create_image(cw // 2, ch // 2, anchor="center", image=photo)

            if self.show_overlays_var.get():
                for action_name, region in regions.items():
                    try:
                        x0, y0, x1, y1 = [float(v) for v in region]
                    except Exception:
                        continue

                    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                        continue

                    x0 = max(0.0, min(1.0, x0))
                    y0 = max(0.0, min(1.0, y0))
                    x1 = max(0.0, min(1.0, x1))
                    y1 = max(0.0, min(1.0, y1))

                    rx0 = ox + int(x0 * nw)
                    ry0 = oy + int(y0 * nh)
                    rx1 = ox + int(x1 * nw)
                    ry1 = oy + int(y1 * nh)
                    canvas.create_rectangle(rx0, ry0, rx1, ry1, outline="#ff0000", width=1)
                    canvas.create_text((rx0 + rx1) // 2, (ry0 + ry1) // 2, text=action_name, fill="#ff0000", font=("Segoe UI", 8, "bold"))

        def on_click(event):
            ox, oy, iw, ih = canvas._image_area
            if iw <= 0 or ih <= 0:
                return

            x = event.x - ox
            y = event.y - oy
            if x < 0 or y < 0 or x > iw or y > ih:
                return

            xp = x / iw
            yp = y / ih
            for action_name, region in regions.items():
                try:
                    x0, y0, x1, y1 = [float(v) for v in region]
                except Exception:
                    continue

                if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                    continue

                if x0 <= xp <= x1 and y0 <= yp <= y1 and action_name in device.commands:
                    rect = canvas.create_rectangle(
                        ox + int(x0 * iw),
                        oy + int(y0 * ih),
                        ox + int(x1 * iw),
                        oy + int(y1 * ih),
                        outline="#00ff00",
                        width=2,
                    )
                    self.root.after(150, lambda rid=rect: canvas.delete(rid))
                    self.send_command(device_name, action_name)
                    return

        canvas.bind("<Configure>", redraw)
        canvas.bind("<Button-1>", on_click)

    def _regions_for_device(self, device_name: str) -> dict[str, tuple[float, float, float, float]]:
        device = self.device_library[device_name]
        regions = self.config_manager.get_overlay_regions(device_name)
        normalized: dict[str, tuple[float, float, float, float]] = {}

        for key, region in regions.items():
            if key in device.commands:
                normalized[key] = region
            else:
                match = next((a for a in device.commands if a.lower() == key.lower()), None)
                if match:
                    normalized[match] = region
                    self.config_manager.set_overlay_region(device_name, match, region)
        return normalized

    def open_overlay_tuner(self, device_name: str):
        if Image is None or ImageTk is None:
            messagebox.showinfo("Tuner unavailable", "Pillow is required for overlay tuning.")
            return

        device = self.device_library[device_name]
        if not device.image_file:
            messagebox.showinfo("Tuner unavailable", f"{device_name} has no image skin.")
            return

        img_path = PROGRAM_DIR / "images" / str(device.image_file)
        if not img_path.exists():
            messagebox.showwarning("Image missing", f"Could not find {img_path.name} in program folder.")
            return

        top = tk.Toplevel(self.root)
        top.title(f"Overlay Tuner - {device_name}")
        self._prepare_dialog(top, self.root)

        pil_image = Image.open(img_path).convert("RGBA")
        width, height = pil_image.size
        photo = ImageTk.PhotoImage(pil_image)

        canvas = tk.Canvas(top, width=width, height=height)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)
        top._photo = photo

        toolbar = ttk.Frame(top, padding=8)
        toolbar.pack(fill="x")

        ttk.Label(toolbar, text="Action:").pack(side="left")
        actions = list(device.commands.keys())
        action_var = tk.StringVar(value=actions[0] if actions else "")
        ttk.OptionMenu(toolbar, action_var, action_var.get(), *actions).pack(side="left", padx=(6, 10))

        coords_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=coords_var).pack(side="left", padx=(0, 10))

        rect_id = None
        start_xy = None

        def on_down(ev):
            nonlocal start_xy, rect_id
            start_xy = (ev.x, ev.y)
            if rect_id:
                canvas.delete(rect_id)
                rect_id = None

        def on_drag(ev):
            nonlocal rect_id
            if not start_xy:
                return
            x0, y0 = start_xy
            x1, y1 = ev.x, ev.y
            if rect_id:
                canvas.delete(rect_id)
            rect_id = canvas.create_rectangle(x0, y0, x1, y1, outline="#00ff00")
            coords_var.set(f"{x0},{y0},{x1},{y1}")

        def on_up(ev):
            nonlocal start_xy
            if not start_xy:
                return
            x0, y0 = start_xy
            coords_var.set(f"{x0},{y0},{ev.x},{ev.y}")
            start_xy = None

        def save_region():
            txt = coords_var.get().strip()
            if not txt:
                messagebox.showwarning("No region", "Draw a region first.")
                return
            try:
                x0, y0, x1, y1 = [int(v) for v in txt.split(",")]
            except Exception:
                messagebox.showwarning("Invalid", "Coordinates are invalid.")
                return

            nx0 = min(x0, x1) / width
            ny0 = min(y0, y1) / height
            nx1 = max(x0, x1) / width
            ny1 = max(y0, y1) / height
            self.config_manager.set_overlay_region(device_name, action_var.get(), (nx0, ny0, nx1, ny1))
            self.config_manager.save()
            self.rebuild_device_tabs()
            messagebox.showinfo("Saved", f"Region saved for {device_name}: {action_var.get()}")

        ttk.Button(toolbar, text="Save Region", command=save_region).pack(side="left")

        canvas.bind("<Button-1>", on_down)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_up)
        self._center_window(top, self.root)

    def clear_device_overlays(self, device_name: str):
        self.config_manager.clear_overlay_regions(device_name)
        self.config_manager.save()
        self.rebuild_device_tabs()
        self.status_var.set(f"Cleared overlays for {device_name}")

    def open_add_device_dialog(self):
        top = tk.Toplevel(self.root)
        top.title("Add Device")
        top.geometry("760x620")
        top.minsize(680, 560)
        self._prepare_dialog(top, self.root)

        tabs = ttk.Notebook(top)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)

        existing_tab = ttk.Frame(tabs, padding=10)
        custom_tab = ttk.Frame(tabs, padding=10)
        tabs.add(existing_tab, text="Existing Templates")
        tabs.add(custom_tab, text="Custom Device")

        all_names = sorted(self.device_library.keys())
        available = [n for n in all_names if n not in self.enabled_devices]

        ttk.Label(existing_tab, text="Available devices:").pack(anchor="w")
        listbox = tk.Listbox(existing_tab, selectmode="single")
        listbox.pack(fill="both", expand=True, pady=(6, 10))
        for name in available:
            listbox.insert(tk.END, name)

        def add_selected_existing():
            selection = listbox.curselection()
            if not selection:
                return
            device_name = listbox.get(selection[0])
            self.enabled_devices = sorted(set(self.enabled_devices + [device_name]))
            self.config_manager.set_enabled_devices(self.enabled_devices)
            self.config_manager.save()
            self.rebuild_device_tabs()
            self.apply_hotkeys()
            self.status_var.set(f"Added device: {device_name}")
            top.destroy()

        ttk.Button(existing_tab, text="Add Selected", command=add_selected_existing).pack(anchor="w")

        custom_tab.grid_columnconfigure(0, weight=1)
        custom_tab.grid_rowconfigure(3, weight=1)

        ttk.Label(
            custom_tab,
            text="Create a custom device, choose how it should look, then map each action to an IR HEX command.",
            foreground="#4b5563",
            wraplength=620,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 10))

        details_frame = ttk.LabelFrame(custom_tab, text="Device Details", padding=10)
        details_frame.grid(row=1, column=0, sticky="ew")
        details_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(details_frame, text="Device Name:").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=name_var, width=32).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 8))

        appearance_frame = ttk.LabelFrame(custom_tab, text="Appearance", padding=10)
        appearance_frame.grid(row=2, column=0, sticky="ew", pady=(10, 10))
        appearance_frame.grid_columnconfigure(1, weight=1)

        skin_mode_var = tk.StringVar(value="Classic")
        skin_mode_values = [*BASE_SKIN_OPTIONS, "Custom Image"]
        ttk.Label(appearance_frame, text="Use:").grid(row=0, column=0, sticky="w")
        skin_mode_combo = ttk.Combobox(
            appearance_frame,
            state="readonly",
            values=skin_mode_values,
            textvariable=skin_mode_var,
            width=20,
        )
        skin_mode_combo.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 8))

        ttk.Label(appearance_frame, text="Image File:").grid(row=1, column=0, sticky="w")
        image_var = tk.StringVar()
        image_row = ttk.Frame(appearance_frame)
        image_row.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(0, 4))
        image_entry = ttk.Entry(image_row, textvariable=image_var, width=28)
        image_entry.pack(side="left", fill="x", expand=True)

        def browse_image():
            source = filedialog.askopenfilename(
                title="Select image",
                filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.gif;*.webp"), ("All Files", "*.*")],
                parent=top,
            )
            if not source:
                return
            stored_name = self._store_custom_image(source)
            if stored_name:
                image_var.set(stored_name)
                skin_mode_var.set("Custom Image")
                update_image_controls()

        browse_button = ttk.Button(image_row, text="Browse...", command=browse_image)
        browse_button.pack(side="left", padx=(6, 0))

        image_hint_var = tk.StringVar(value="Choose Custom Image to enable image import into the images folder.")
        ttk.Label(appearance_frame, textvariable=image_hint_var, foreground="#4b5563", wraplength=520).grid(row=2, column=1, sticky="w")

        def update_image_controls(_event=None):
            use_custom_image = skin_mode_var.get() == "Custom Image"
            entry_state = "normal" if use_custom_image else "disabled"
            image_entry.configure(state=entry_state)
            browse_button.configure(state=entry_state)
            if use_custom_image:
                image_hint_var.set("The selected image is copied into the images folder and can be reused later.")
            else:
                image_hint_var.set("Classic and Slate use the built-in layouts. Switch to Custom Image to browse artwork.")

        skin_mode_combo.bind("<<ComboboxSelected>>", update_image_controls)
        update_image_controls()

        commands_frame = ttk.LabelFrame(custom_tab, text="Actions and HEX", padding=10)
        commands_frame.grid(row=3, column=0, sticky="nsew")
        commands_frame.grid_columnconfigure(0, weight=1)

        table = ttk.Frame(commands_frame)
        table.pack(fill="both", expand=True)

        ttk.Label(table, text="Action", width=22).grid(row=0, column=0, sticky="w")
        ttk.Label(table, text="HEX", width=50).grid(row=0, column=1, sticky="w")

        rows_host = ttk.Frame(table)
        rows_host.grid(row=1, column=0, columnspan=2, sticky="nsew")
        table.grid_columnconfigure(1, weight=1)

        action_rows: list[tuple[tk.StringVar, tk.StringVar, ttk.Frame]] = []

        def add_action_row(action_text: str = "", hex_text: str = ""):
            row_frame = ttk.Frame(rows_host)
            row_frame.pack(fill="x", pady=(0, 4))
            action_var = tk.StringVar(value=action_text)
            hex_var = tk.StringVar(value=hex_text)

            ttk.Entry(row_frame, textvariable=action_var, width=20).pack(side="left")
            ttk.Entry(row_frame, textvariable=hex_var).pack(side="left", fill="x", expand=True, padx=(6, 6))

            def remove_row():
                row_frame.destroy()
                try:
                    action_rows.remove((action_var, hex_var, row_frame))
                except ValueError:
                    pass

            ttk.Button(row_frame, text="Remove", command=remove_row).pack(side="left")
            action_rows.append((action_var, hex_var, row_frame))

        add_action_row("Power", "0000 ...")
        add_action_row("Volume Up", "0000 ...")

        footer_row = ttk.Frame(custom_tab)
        footer_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(footer_row, text="Add Action Row", command=lambda: add_action_row("", "")).pack(side="left")

        def add_custom_device():
            device_name = name_var.get().strip()
            if not device_name:
                messagebox.showwarning("Missing name", "Enter a device name.")
                return

            commands: dict[str, str] = {}
            for action_var, hex_var, _row in list(action_rows):
                action_name = (action_var.get() or "").strip()
                command_hex = (hex_var.get() or "").strip()
                if not action_name and not command_hex:
                    continue
                if not action_name or not command_hex:
                    messagebox.showwarning("Invalid action row", "Each action row must include both Action and HEX.")
                    return
                commands[action_name] = command_hex

            if not commands:
                messagebox.showwarning("No commands", "Add at least one Action=HEX entry.")
                return

            raw_image_value = image_var.get().strip()
            image_name = self._normalize_custom_image_value(raw_image_value)
            if raw_image_value and not image_name:
                return

            selected_skin = skin_mode_var.get().strip() or "Classic"
            if selected_skin == "Custom Image":
                if not image_name:
                    messagebox.showwarning("Image required", "Choose an image when using the Custom Image appearance.")
                    return
                skin_value = image_name
            else:
                skin_value = selected_skin

            self.config_manager.set_custom_device(device_name, commands, image_name)
            self.config_manager.set_device_skin(device_name, skin_value)
            self.refresh_device_library()
            self.enabled_devices = sorted(set(self.enabled_devices + [device_name]))
            self.config_manager.set_enabled_devices(self.enabled_devices)
            self.config_manager.save()
            self.rebuild_device_tabs()
            self.apply_hotkeys()
            self.status_var.set(f"Custom device created: {device_name}")
            top.destroy()

        ttk.Button(footer_row, text="Create and Add Device", command=add_custom_device).pack(side="right")
        self._center_window(top, self.root)

    def open_remove_device_dialog(self):
        top = tk.Toplevel(self.root)
        top.title("Remove Device")
        top.geometry("420x360")
        self._prepare_dialog(top, self.root)

        ttk.Label(top, text="Configured devices:", padding=10).pack(anchor="w")
        listbox = tk.Listbox(top, selectmode="single")
        listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        for name in self.enabled_devices:
            listbox.insert(tk.END, name)

        delete_profile_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Also delete custom profile definition (if custom)", variable=delete_profile_var).pack(anchor="w", padx=10, pady=(0, 10))

        def remove_selected():
            selection = listbox.curselection()
            if not selection:
                return
            device_name = listbox.get(selection[0])
            self.enabled_devices = [d for d in self.enabled_devices if d != device_name]
            self.config_manager.set_enabled_devices(self.enabled_devices)

            if delete_profile_var.get() and device_name not in BUILTIN_DEVICE_LIBRARY:
                self.config_manager.remove_custom_device(device_name)
                self.config_manager.clear_overlay_regions(device_name)

            self.config_manager.save()
            self.refresh_device_library()
            self.rebuild_device_tabs()
            self.apply_hotkeys()
            self.status_var.set(f"Removed device: {device_name}")
            top.destroy()

        buttons = ttk.Frame(top, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Remove", command=remove_selected).pack(side="left")
        ttk.Button(buttons, text="Close", command=top.destroy).pack(side="right")
        self._center_window(top, self.root)

    def open_edit_device_commands_dialog(self):
        top = tk.Toplevel(self.root)
        top.title("Edit Device Commands")
        top.geometry("920x700")
        top.minsize(840, 620)
        self._prepare_dialog(top, self.root)

        device_names = sorted(self.device_library.keys())
        if not device_names:
            ttk.Label(top, text="No devices available to edit.", padding=12).pack(anchor="w")
            self._center_window(top, self.root)
            return

        default_device = self.last_selected_device or self._selected_notebook_device_name()
        if default_device not in device_names:
            default_device = device_names[0]

        shell = ttk.Frame(top, padding=12)
        shell.pack(fill="both", expand=True)

        selector = ttk.LabelFrame(shell, text="Device", padding=10)
        selector.pack(fill="x", pady=(0, 10))
        ttk.Label(selector, text="Select device:").pack(side="left")
        selected_device = tk.StringVar(value=default_device)
        device_combo = ttk.Combobox(selector, state="readonly", width=28, values=device_names, textvariable=selected_device)
        device_combo.pack(side="left", padx=(8, 12))

        appearance = ttk.LabelFrame(shell, text="Appearance", padding=10)
        appearance.pack(fill="x", pady=(0, 10))
        appearance.grid_columnconfigure(1, weight=1)

        ttk.Label(appearance, text="Image File:").grid(row=0, column=0, sticky="w")
        image_var = tk.StringVar()
        image_row = ttk.Frame(appearance)
        image_row.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        image_entry = ttk.Entry(image_row, textvariable=image_var)
        image_entry.pack(side="left", fill="x", expand=True)

        def browse_image():
            source = filedialog.askopenfilename(
                title="Select image",
                filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.gif;*.webp"), ("All Files", "*.*")],
                parent=top,
            )
            if not source:
                return
            stored_name = self._store_custom_image(source)
            if stored_name:
                image_var.set(stored_name)
                skin_mode_var.set("Custom Image")
                refresh_image_controls()

        browse_button = ttk.Button(image_row, text="Browse...", command=browse_image)
        browse_button.pack(side="left", padx=(6, 0))

        ttk.Label(appearance, text="Skin:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        skin_mode_var = tk.StringVar(value="Classic")
        skin_mode_values = [*BASE_SKIN_OPTIONS, "Custom Image"]
        skin_combo = ttk.Combobox(appearance, state="readonly", values=skin_mode_values, textvariable=skin_mode_var, width=20)
        skin_combo.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        image_hint_var = tk.StringVar(value="Classic and Slate use built-in layouts. Choose Custom Image to enable image selection.")
        ttk.Label(appearance, textvariable=image_hint_var, foreground="#4b5563", wraplength=620).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        def refresh_image_controls(_event=None):
            use_custom_image = skin_mode_var.get() == "Custom Image"
            state = "normal" if use_custom_image else "disabled"
            image_entry.configure(state=state)
            browse_button.configure(state=state)
            image_hint_var.set(
                "Choose an image file when using Custom Image." if use_custom_image else "Classic and Slate use built-in layouts. Choose Custom Image to enable image selection."
            )

        skin_combo.bind("<<ComboboxSelected>>", refresh_image_controls)

        commands = ttk.LabelFrame(shell, text="Commands and Hotkeys", padding=10)
        commands.pack(fill="both", expand=True)

        header = ttk.Frame(commands)
        header.pack(fill="x")
        ttk.Label(header, text="Action", width=18).pack(side="left")
        ttk.Label(header, text="HEX", width=48).pack(side="left", padx=(6, 0))
        ttk.Label(header, text="Hotkey", width=16).pack(side="left", padx=(6, 0))
        ttk.Label(header, text="Toggle With", width=16).pack(side="left", padx=(6, 0))

        rows_view = ttk.Frame(commands)
        rows_view.pack(fill="both", expand=True, pady=(6, 0))

        ui_bg = ttk.Style(top).lookup("TFrame", "background") or top.cget("bg")
        rows_canvas = tk.Canvas(rows_view, highlightthickness=0, borderwidth=0, bg=ui_bg)
        rows_scrollbar = ttk.Scrollbar(rows_view, orient="vertical", command=rows_canvas.yview)
        rows_canvas.configure(yscrollcommand=rows_scrollbar.set)
        rows_canvas.pack(side="left", fill="both", expand=True)

        rows_container = ttk.Frame(rows_canvas)
        rows_window_id = rows_canvas.create_window((0, 0), window=rows_container, anchor="nw")
        scrollbar_visible = False

        def _update_rows_scrollbar_visibility():
            nonlocal scrollbar_visible
            bbox = rows_canvas.bbox("all")
            content_height = (bbox[3] - bbox[1]) if bbox else 0
            viewport_height = rows_canvas.winfo_height()
            needs_scroll = content_height > (viewport_height + 1)

            if needs_scroll and not scrollbar_visible:
                rows_scrollbar.pack(side="right", fill="y")
                scrollbar_visible = True
            elif not needs_scroll and scrollbar_visible:
                rows_scrollbar.pack_forget()
                scrollbar_visible = False

        def _sync_rows_scrollregion(_event=None):
            rows_canvas.configure(scrollregion=rows_canvas.bbox("all"))
            _update_rows_scrollbar_visibility()

        def _sync_rows_width(_event):
            rows_canvas.itemconfigure(rows_window_id, width=_event.width)
            _update_rows_scrollbar_visibility()

        rows_container.bind("<Configure>", _sync_rows_scrollregion)
        rows_canvas.bind("<Configure>", _sync_rows_width)

        action_rows: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar, tk.StringVar, ttk.Frame]] = []

        def clear_rows():
            for _action, _hex, _hotkey, _toggle_with, frame in list(action_rows):
                frame.destroy()
            action_rows.clear()

        def add_row(action_text: str = "", hex_text: str = "", hotkey_text: str = "", toggle_with: str = ""):
            row_frame = ttk.Frame(rows_container)
            row_frame.pack(fill="x", pady=(0, 4))

            action_var = tk.StringVar(value=action_text)
            hex_var = tk.StringVar(value=hex_text)
            hotkey_var = tk.StringVar(value=hotkey_text)
            toggle_with_var = tk.StringVar(value=(toggle_with or "").strip())

            ttk.Entry(row_frame, textvariable=action_var, width=18).pack(side="left")
            ttk.Entry(row_frame, textvariable=hex_var).pack(side="left", padx=(6, 6), fill="x", expand=True)
            ttk.Entry(row_frame, textvariable=hotkey_var, width=14, state="readonly").pack(side="left")
            ttk.Entry(row_frame, textvariable=toggle_with_var, width=14).pack(side="left", padx=(6, 0))

            def learn_row():
                action_name = (action_var.get() or "").strip() or "(unnamed action)"
                selected = self.serial_port_var.get().strip()
                configured_port = None if selected.lower() == "auto" else selected
                self.status_var.set(f"Learning IR for {selected_device.get()}: {action_name}...")

                dialog = tk.Toplevel(top)
                dialog.title(f"Learn Command - {action_name}")
                dialog.geometry("860x210")
                dialog.minsize(700, 190)
                self._prepare_dialog(dialog, top)

                shell2 = ttk.Frame(dialog, padding=12)
                shell2.pack(fill="both", expand=True)

                ttk.Label(shell2, text=(
                    "Press the source remote button near the Arduino receiver, then click Capture. "
                    "When a code appears, click OK to apply it to this row."
                ), foreground="#4b5563", wraplength=820, justify="left").pack(anchor="w", pady=(0, 8))

                captured_var = tk.StringVar(value="")
                capture_status_var = tk.StringVar(value="Waiting for capture...")

                row = ttk.Frame(shell2)
                row.pack(fill="x")
                ttk.Label(row, text="Captured HEX", width=14).pack(side="left")
                ttk.Entry(row, textvariable=captured_var).pack(side="left", fill="x", expand=True)

                ttk.Label(shell2, textvariable=capture_status_var, foreground="#2563eb").pack(anchor="w", pady=(8, 0))

                controls2 = ttk.Frame(shell2)
                controls2.pack(fill="x", pady=(10, 0))

                cancel_event = threading.Event()

                def cancel_capture():
                    cancel_event.set()
                    if dialog.winfo_exists():
                        dialog.destroy()

                dialog.protocol("WM_DELETE_WINDOW", cancel_capture)

                capture_button = ttk.Button(controls2, text="Capture")
                capture_button.pack(side="left")
                ok_button = ttk.Button(controls2, text="OK", state="disabled")
                ok_button.pack(side="left", padx=(8, 0))
                ttk.Button(controls2, text="Cancel", command=cancel_capture).pack(side="right")

                def _apply_capture(used_port: str, pronto: str):
                    if not dialog.winfo_exists():
                        return
                    captured_var.set(pronto)
                    capture_status_var.set(f"Capture complete from {used_port}. Click OK to use this code.")
                    self.status_var.set(f"Learned {action_name} via {used_port}")
                    self.set_connection_state(True, used_port)
                    ok_button.configure(state="normal")
                    capture_button.configure(state="normal")

                def _capture_failed(msg: str):
                    if not dialog.winfo_exists():
                        return
                    capture_status_var.set(f"Capture failed: {msg}")
                    self.status_var.set(f"Learn failed: {msg}")
                    self.set_connection_state(False, "Learn failed")
                    capture_button.configure(state="normal")

                def start_capture():
                    cancel_event.clear()
                    capture_button.configure(state="disabled")
                    capture_status_var.set("Capturing... press the remote button now.")

                    def _do_learn():
                        try:
                            result = learn_ir_command(port=configured_port, cancel_event=cancel_event)
                            if result is None:
                                return
                            used_port, pronto = result
                            self.root.after(0, lambda: _apply_capture(used_port, pronto))
                        except IRSendError as exc:
                            if cancel_event.is_set():
                                return
                            msg = str(exc)
                            self.root.after(0, lambda: _capture_failed(msg))
                        except Exception as exc:
                            if cancel_event.is_set():
                                return
                            msg = str(exc)
                            self.root.after(0, lambda: _capture_failed(f"Unexpected error: {msg}"))

                    threading.Thread(target=_do_learn, daemon=True).start()

                def accept_capture():
                    learned_hex = (captured_var.get() or "").strip()
                    if not learned_hex:
                        messagebox.showwarning("No capture", "Capture a code before clicking OK.")
                        return
                    hex_var.set(learned_hex)
                    cancel_capture()

                capture_button.configure(command=start_capture)
                ok_button.configure(command=accept_capture)
                self._center_window(dialog, self.root)
                start_capture()

            def edit_hotkey():
                current = (hotkey_var.get() or "").strip()
                updated = self._capture_hotkey_dialog(top, f"Hotkey - {action_var.get() or 'Action'}", current)
                if updated is not None:
                    hotkey_var.set(updated)

            ttk.Button(row_frame, text="Learn", command=learn_row).pack(side="left", padx=(0, 6))
            ttk.Button(row_frame, text="Hotkey", command=edit_hotkey).pack(side="left", padx=(0, 6))

            def remove_row():
                row_frame.destroy()
                try:
                    action_rows.remove((action_var, hex_var, hotkey_var, toggle_with_var, row_frame))
                except ValueError:
                    pass

            ttk.Button(row_frame, text="Remove", command=remove_row).pack(side="left")
            action_rows.append((action_var, hex_var, hotkey_var, toggle_with_var, row_frame))

        def load_device(name: str):
            device = self.device_library[name]
            image_var.set(str(device.image_file or ""))
            current_skin = self.config_manager.get_device_skin(name)
            if current_skin == "Custom Image" and device.image_file:
                skin_mode_var.set("Custom Image")
            elif current_skin in BASE_SKIN_OPTIONS:
                skin_mode_var.set(current_skin)
            elif device.image_file and current_skin == device.image_file:
                skin_mode_var.set("Custom Image")
                image_var.set(str(device.image_file))
            else:
                skin_mode_var.set("Classic")
            refresh_image_controls()

            clear_rows()
            for action_name, command_hex in device.commands.items():
                add_row(
                    action_name,
                    command_hex,
                    self.config_manager.get_hotkey(name, action_name),
                    self.config_manager.get_hotkey_toggle_partner(name, action_name),
                )
            if not device.commands:
                add_row("Power", "0000 ...", "", "")

        def save_commands():
            device_name = selected_device.get().strip()
            if not device_name:
                return

            commands: dict[str, str] = {}
            toggle_map: dict[str, str] = {}

            for action_var, hex_var, hotkey_var, toggle_with_var, _frame in list(action_rows):
                action_name = (action_var.get() or "").strip()
                command_hex = (hex_var.get() or "").strip()
                hotkey_text = (hotkey_var.get() or "").strip()
                toggle_with_text = (toggle_with_var.get() or "").strip()
                if not action_name and not command_hex and not hotkey_text:
                    continue
                if not action_name or not command_hex:
                    messagebox.showwarning("Invalid row", "Each non-empty row needs both Action and HEX.")
                    return

                commands[action_name] = command_hex
                self.config_manager.set_hotkey(device_name, action_name, hotkey_text)
                toggle_map[action_name] = toggle_with_text

            for action_name, partner in list(toggle_map.items()):
                if not partner:
                    continue
                if partner == action_name:
                    messagebox.showwarning("Invalid toggle pair", f"{action_name} cannot toggle with itself.")
                    return
                if partner not in commands:
                    messagebox.showwarning("Invalid toggle pair", f"Toggle partner '{partner}' for '{action_name}' was not found in this device.")
                    return

            for action_name, partner in list(toggle_map.items()):
                if not partner:
                    continue
                existing = toggle_map.get(partner, "")
                if existing and existing != action_name:
                    messagebox.showwarning("Invalid toggle pair", f"'{partner}' is already paired with '{existing}'.")
                    return
                toggle_map[partner] = action_name

            if not commands:
                messagebox.showwarning("No commands", "Add at least one Action/HEX row.")
                return

            raw_image_value = image_var.get().strip()
            image_name = self._normalize_custom_image_value(raw_image_value)
            if raw_image_value and not image_name:
                return

            selected_skin = skin_mode_var.get().strip() or "Classic"
            if selected_skin == "Custom Image":
                if not image_name:
                    messagebox.showwarning("Image required", "Choose an image when using the Custom Image appearance.")
                    return
                skin_value = "Custom Image"
            else:
                skin_value = selected_skin

            self.config_manager.set_custom_device(device_name, commands, image_name)
            self.config_manager.set_device_skin(device_name, skin_value)

            for action_name in commands:
                self.config_manager.set_hotkey_toggle_partner(device_name, action_name, toggle_map.get(action_name, ""))

            self.config_manager.set_enabled_devices(sorted(set(self.enabled_devices + [device_name])))
            self.config_manager.save()

            self.refresh_device_library()
            self.enabled_devices = sorted(set(self.enabled_devices + [device_name]))
            self.rebuild_device_tabs()
            self.apply_hotkeys()
            self.status_var.set(f"Saved commands for {device_name}")
            messagebox.showinfo("Saved", f"Updated command profile for {device_name}.")

        device_combo.bind("<<ComboboxSelected>>", lambda _event: load_device(selected_device.get()))
        load_device(selected_device.get())

        controls = ttk.Frame(shell, padding=(0, 10, 0, 0))
        controls.pack(fill="x")
        ttk.Button(controls, text="Add Row", command=lambda: add_row("", "", "")).pack(side="left")
        ttk.Button(controls, text="Save", command=save_commands).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Close", command=top.destroy).pack(side="right")
        help_text = (
            "Use Learn to capture HEX and Hotkey to assign shortcuts. "
            "To alternate two actions on one hotkey, set each row's Toggle With to the other action name."
        )
        ttk.Button(controls, text="?", width=3, command=lambda: messagebox.showinfo("Device Commands Help", help_text)).pack(side="left", padx=(12, 0))
        self._center_window(top, self.root)

    def open_settings_dialog(self):
        top = tk.Toplevel(self.root)
        top.title("Device Setup")
        top.geometry("820x390")
        top.minsize(720, 360)
        self._prepare_dialog(top, self.root)

        shell = ttk.Frame(top, padding=(10, 6, 10, 6))
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="Device Setup", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        ttk.Label(shell, text="Manage device profiles and Arduino Compatible Device connection.", foreground="#4b5563").pack(anchor="w", pady=(1, 4))

        action_frame = ttk.LabelFrame(shell, text="Library Actions", padding=10)
        action_frame.pack(fill="x", pady=(0, 4))

        row = ttk.Frame(action_frame)
        row.pack(fill="x")
        ttk.Button(row, text="Add Device", command=self.open_add_device_dialog).pack(side="left")
        ttk.Button(row, text="Remove Device", command=self.open_remove_device_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Edit Device Commands", command=self.open_edit_device_commands_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Import Profile", command=self.import_device_profile).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Export Profile", command=self.open_export_device_dialog).pack(side="left", padx=(8, 0))

        connection_frame = ttk.LabelFrame(shell, text="Arduino Compatible Device Connection", padding=10)
        connection_frame.pack(fill="x")

        port_row = ttk.Frame(connection_frame)
        port_row.pack(fill="x")
        ttk.Label(port_row, text="Serial Port", width=14).pack(side="left")

        port_values = ["Auto", *list_serial_ports()]
        selected_port = self.serial_port_var.get().strip() or "Auto"
        if selected_port not in port_values:
            port_values.append(selected_port)

        port_combo = ttk.Combobox(port_row, width=18, state="readonly", values=port_values, textvariable=self.serial_port_var)
        port_combo.pack(side="left")

        def refresh_ports():
            values = ["Auto", *list_serial_ports()]
            current = self.serial_port_var.get().strip() or "Auto"
            if current not in values:
                values.append(current)
            port_combo["values"] = values

        ttk.Button(port_row, text="Refresh", command=refresh_ports).pack(side="left", padx=(8, 0))

        def test_connection():
            selected = self.serial_port_var.get().strip()
            configured_port = None if selected.lower() == "auto" else selected
            self.status_var.set("Testing connection...")

            def _do_test():
                try:
                    used_port, response = test_arduino_connection(port=configured_port)
                    self.root.after(0, lambda: (
                        self.status_var.set(f"Arduino Compatible Device connection OK on {used_port}"),
                        self.set_connection_state(True, used_port),
                        messagebox.showinfo("Connection Test", f"Connected to Arduino Compatible Device on {used_port}"),
                    ))
                except IRSendError as exc:
                    msg = str(exc)
                    self.root.after(0, lambda: (
                        self.status_var.set(f"Connection test failed: {msg}"),
                        self.set_connection_state(False, "Test failed"),
                        messagebox.showerror("Connection Test", f"Unable to verify Arduino Compatible Device connection.\n{msg}"),
                    ))
                except Exception as exc:
                    msg = str(exc)
                    self.root.after(0, lambda: (
                        self.status_var.set(f"Connection test failed: {msg}"),
                        self.set_connection_state(False, "Test failed"),
                        messagebox.showerror("Connection Test", f"Unexpected error while testing Arduino Compatible Device connection.\n{msg}"),
                    ))

            threading.Thread(target=_do_test, daemon=True).start()

        ttk.Button(port_row, text="Test Connection", command=test_connection).pack(side="left", padx=(8, 0))
        ttk.Label(connection_frame, text="Use Auto for Arduino Compatible Device detection, or choose a fixed COM port.", foreground="#4b5563").pack(anchor="w", pady=(2, 0))

        def apply_settings():
            self.config_manager.set_serial_port("" if (self.serial_port_var.get().strip().lower() == "auto") else self.serial_port_var.get().strip())
            self.config_manager.save()
            self.status_var.set("Device Setup saved")

        buttons = ttk.Frame(shell, padding=(0, 4, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Apply", command=apply_settings).pack(side="left")
        ttk.Button(buttons, text="Close", command=top.destroy).pack(side="right")
        self._center_window(top, self.root)

    def import_device_profile(self):
        path = filedialog.askopenfilename(
            title="Import Device Profile",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            messagebox.showerror("Import failed", f"Unable to read JSON profile.\n{exc}")
            return

        imported_count = 0
        profiles = payload if isinstance(payload, list) else [payload]

        def resolve_conflict(name: str) -> tuple[str, str]:
            choice_var = tk.StringVar(value="skip")
            rename_var = tk.StringVar(value=f"{name} (Imported)")
            result = {"action": "skip", "name": name}

            dlg = tk.Toplevel(self.root)
            dlg.title("Import Conflict")
            dlg.resizable(False, False)
            self._prepare_dialog(dlg, self.root)

            ttk.Label(dlg, text=f"A device named '{name}' already exists.", padding=10).pack(anchor="w")
            ttk.Label(dlg, text="Choose how to handle this import:", padding=(10, 0)).pack(anchor="w")

            ttk.Radiobutton(dlg, text="Overwrite existing", value="overwrite", variable=choice_var).pack(anchor="w", padx=14, pady=(6, 0))
            ttk.Radiobutton(dlg, text="Rename imported", value="rename", variable=choice_var).pack(anchor="w", padx=14)
            ttk.Radiobutton(dlg, text="Skip", value="skip", variable=choice_var).pack(anchor="w", padx=14)

            row = ttk.Frame(dlg, padding=(10, 6, 10, 0))
            row.pack(fill="x")
            ttk.Label(row, text="New name:").pack(side="left")
            ttk.Entry(row, textvariable=rename_var, width=28).pack(side="left", padx=(6, 0))

            def submit():
                result["action"] = choice_var.get()
                result["name"] = (rename_var.get() or "").strip() or name
                dlg.destroy()

            buttons = ttk.Frame(dlg, padding=10)
            buttons.pack(fill="x")
            ttk.Button(buttons, text="OK", command=submit).pack(side="left")
            ttk.Button(buttons, text="Cancel", command=dlg.destroy).pack(side="right")

            self._center_window(dlg, self.root)
            dlg.wait_window()
            return result["action"], result["name"]

        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name", "")).strip()
            commands = profile.get("commands", {})
            image_file = profile.get("image_file")
            skin_raw = profile.get("skin")
            default_skin = str(image_file).strip() if image_file else "Classic"
            skin = str(skin_raw).strip() if skin_raw is not None else default_skin
            if not skin:
                skin = default_skin

            if not name or not isinstance(commands, dict) or not commands:
                continue

            normalized_commands = {}
            for action_name, command_hex in commands.items():
                a = str(action_name).strip()
                h = str(command_hex).strip()
                if a and h:
                    normalized_commands[a] = h
            if not normalized_commands:
                continue

            if name in self.device_library:
                action, resolved_name = resolve_conflict(name)
                if action == "skip":
                    continue
                if action == "rename":
                    name = resolved_name

            normalized_image = str(image_file).strip() if image_file else None
            self.config_manager.set_custom_device(name, normalized_commands, normalized_image)
            allowed = set(BASE_SKIN_OPTIONS)
            if normalized_image:
                allowed.add(normalized_image)
            self.config_manager.set_device_skin(name, skin if skin in allowed else (normalized_image or "Classic"))
            self.enabled_devices = sorted(set(self.enabled_devices + [name]))
            self.refresh_device_library()
            imported_count += 1

        if imported_count == 0:
            messagebox.showwarning("Import", "No valid device profiles were found in the file.")
            return

        self.config_manager.set_enabled_devices(self.enabled_devices)
        self.config_manager.save()
        self.refresh_device_library()
        self.rebuild_device_tabs()
        self.apply_hotkeys()
        self.status_var.set(f"Imported {imported_count} device profile(s)")

    def open_export_device_dialog(self):
        if not self.device_library:
            messagebox.showinfo("Export", "No device definitions are available to export.")
            return

        top = tk.Toplevel(self.root)
        top.title("Export Device Profile")
        top.geometry("420x340")
        self._prepare_dialog(top, self.root)

        ttk.Label(top, text="Select a device to export:", padding=10).pack(anchor="w")
        listbox = tk.Listbox(top, selectmode="single")
        listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        for name in sorted(self.device_library.keys()):
            listbox.insert(tk.END, name)

        def export_selected():
            selection = listbox.curselection()
            if not selection:
                return
            device_name = listbox.get(selection[0])
            device = self.device_library[device_name]

            path = filedialog.asksaveasfilename(
                title="Export Device Profile",
                initialfile=f"{device_name}.json",
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
                parent=top,
            )
            if not path:
                return

            payload = {
                "name": device_name,
                "image_file": device.image_file,
                "commands": dict(device.commands),
                "skin": self._device_skin(device_name),
                "is_custom": device_name not in BUILTIN_DEVICE_LIBRARY,
            }
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                self.status_var.set(f"Exported profile: {device_name}")
                top.destroy()
            except Exception as exc:
                messagebox.showerror("Export failed", f"Unable to export profile.\n{exc}")

        buttons = ttk.Frame(top, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Export", command=export_selected).pack(side="left")
        ttk.Button(buttons, text="Close", command=top.destroy).pack(side="right")
        self._center_window(top, self.root)

    def open_about_dialog(self):
        top = tk.Toplevel(self.root)
        top.title("About")
        top.geometry("620x250")
        top.minsize(540, 240)
        self._prepare_dialog(top, self.root)

        shell = ttk.Frame(top, padding=12)
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="IR Remote Sender", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(shell, text=f"Version {self.app_version}", font=("Segoe UI", 10, "bold"), foreground="#374151").pack(anchor="w", pady=(2, 4))
        ttk.Label(shell, text="Created by HoochWindgrass - June 2026", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 8))

        about_text = (
            "Desktop app for learning, storing, and sending IR commands.\n\n"
            "Languages: Python, Arduino/C++.\n"
            "Modules: tkinter, pyserial, Pillow, pystray, PyInstaller.\n"
            "Hardware: Arduino Compatible Device, IR receiver, IR LED transmitter, USB serial."
        )
        ttk.Label(shell, text=about_text, wraplength=580, justify="left", foreground="#374151").pack(anchor="w", pady=(0, 8))

        ttk.Button(shell, text="Close", command=top.destroy).pack(anchor="e")
        self._center_window(top, self.root)

    def save_hotkeys(self):
        for device_name, action_vars in self.hotkey_vars.items():
            for action_name, var in action_vars.items():
                self.config_manager.set_hotkey(device_name, action_name, (var.get() or "").strip())

    def clear_hotkeys(self):
        actions_by_device = {
            name: list(self.device_library[name].commands.keys())
            for name in self.enabled_devices
            if name in self.device_library
        }
        self.config_manager.clear_hotkeys_for_devices(self.enabled_devices, actions_by_device)
        self.config_manager.save()
        for action_vars in self.hotkey_vars.values():
            for var in action_vars.values():
                var.set("")
        self.apply_hotkeys()
        self.status_var.set("Hotkeys cleared")

    def apply_hotkeys(self):
        # Remove existing app-scoped bindings.
        for sequence in set(self.bindings.values()):
            self.root.unbind(sequence)
        self.bindings.clear()
        self.hotkey_toggle_state.clear()

        # Remove existing global bindings.
        if self.global_hotkey_listener is not None:
            try:
                self.global_hotkey_listener.stop()
            except Exception:
                pass
            self.global_hotkey_listener = None
        self.global_hotkey_handles.clear()

        global_groups: dict[str, list[tuple[int, str, str, str]]] = {}
        local_groups: dict[str, list[tuple[int, str, str, str]]] = {}
        action_sequence = 0

        for device_name in self.enabled_devices:
            device = self.device_library.get(device_name)
            if not device:
                continue
            for action_name in device.commands:
                raw = self.config_manager.get_hotkey(device_name, action_name).strip()
                if not raw:
                    continue

                global_sequence = self._normalize_pynput_hotkey(raw)
                sequence = self._normalize_sequence(raw)
                toggle_partner = self.config_manager.get_hotkey_toggle_partner(device_name, action_name)

                if global_sequence:
                    global_groups.setdefault(global_sequence, []).append((action_sequence, device_name, action_name, toggle_partner))
                if sequence:
                    local_groups.setdefault(sequence, []).append((action_sequence, device_name, action_name, toggle_partner))

                action_sequence += 1

        globally_bound_actions: set[tuple[str, str]] = set()

        if pynput_keyboard is not None and global_groups:
            callbacks: dict[str, object] = {}
            for global_sequence, action_group in global_groups.items():
                group_key = f"global:{global_sequence}"
                ordered_actions = self._build_hotkey_dispatch_group(action_group)
                group_tuple = tuple(ordered_actions)
                callbacks[global_sequence] = (
                    lambda gk=group_key, grp=group_tuple: self.root.after(0, lambda: self._dispatch_hotkey_group(gk, grp))
                )
                for _idx, d, a, _partner in action_group:
                    globally_bound_actions.add((d, a))

            try:
                self.global_hotkey_listener = pynput_keyboard.GlobalHotKeys(callbacks)
                self.global_hotkey_listener.start()
            except Exception:
                self.global_hotkey_listener = None
                globally_bound_actions.clear()

        for sequence, action_group in local_groups.items():
            ordered_actions = self._build_hotkey_dispatch_group(action_group)
            filtered_group = [action_ref for action_ref in ordered_actions if action_ref not in globally_bound_actions]
            if not filtered_group:
                continue

            group_key = f"local:{sequence}"
            group_tuple = tuple(filtered_group)
            self.root.bind(sequence, lambda _event, gk=group_key, grp=group_tuple: self._dispatch_hotkey_group(gk, grp))
            self.bindings[("local", sequence)] = sequence

    def _build_hotkey_dispatch_group(self, action_group: list[tuple[int, str, str, str]]) -> list[tuple[str, str]]:
        ordered = sorted(action_group, key=lambda item: item[0])
        index_by_action = {(device_name, action_name): idx for idx, (_order, device_name, action_name, _partner) in enumerate(ordered)}

        used_indices: set[int] = set()
        dispatch: list[tuple[str, str]] = []

        for idx, (_order, device_name, action_name, partner_name) in enumerate(ordered):
            if idx in used_indices:
                continue

            partner_idx = index_by_action.get((device_name, partner_name)) if partner_name else None
            if partner_idx is not None and partner_idx != idx and partner_idx not in used_indices:
                dispatch.append((device_name, action_name))
                partner_entry = ordered[partner_idx]
                dispatch.append((partner_entry[1], partner_entry[2]))
                used_indices.add(idx)
                used_indices.add(partner_idx)
            else:
                dispatch.append((device_name, action_name))
                used_indices.add(idx)

        return dispatch

    def _dispatch_hotkey_group(self, group_key: str, action_group: tuple[tuple[str, str], ...]):
        if not action_group:
            return

        idx = self.hotkey_toggle_state.get(group_key, 0)
        if idx >= len(action_group):
            idx = 0

        device_name, action_name = action_group[idx]
        self.hotkey_toggle_state[group_key] = (idx + 1) % len(action_group)
        self.send_command(device_name, action_name)

    def _normalize_pynput_hotkey(self, sequence: str) -> str | None:
        raw = (sequence or "").strip()
        if not raw:
            return None

        parts = [part.strip() for part in raw.split("+") if part.strip()]
        if not parts:
            return None

        mapping = {
            "ctrl": "ctrl",
            "control": "ctrl",
            "alt": "alt",
            "shift": "shift",
            "cmd": "cmd",
            "command": "cmd",
            "windows": "cmd",
            "win": "cmd",
            "enter": "enter",
            "return": "enter",
            "esc": "esc",
            "escape": "esc",
            "space": "space",
            "pgup": "page_up",
            "pageup": "page_up",
            "pgdn": "page_down",
            "pagedown": "page_down",
            "left": "left",
            "right": "right",
            "up": "up",
            "down": "down",
        }

        modifier_tokens = {"ctrl", "alt", "shift", "cmd"}
        modifiers: list[str] = []
        key_token: str | None = None

        for part in parts:
            lower = part.lower()
            token = mapping.get(lower, lower)
            if token in modifier_tokens:
                wrapped = f"<{token}>"
                if wrapped not in modifiers:
                    modifiers.append(wrapped)
                continue

            if len(token) == 1 and token.isalnum():
                key_token = token.lower()
            else:
                key_token = f"<{token}>"

        if not key_token:
            return None

        return "+".join([*modifiers, key_token])

    def _normalize_sequence(self, sequence: str) -> str | None:
        raw = (sequence or "").strip()
        if not raw:
            return None
        if raw.startswith("<") and raw.endswith(">"):
            return raw

        parts = [part.strip() for part in raw.split("+") if part.strip()]
        if not parts:
            return None

        mapping = {
            "ctrl": "Control",
            "control": "Control",
            "alt": "Alt",
            "shift": "Shift",
            "cmd": "Command",
            "command": "Command",
            "windows": "Command",
            "enter": "Return",
            "return": "Return",
            "esc": "Escape",
            "escape": "Escape",
            "space": "space",
            "pgup": "Prior",
            "pageup": "Prior",
            "pgdn": "Next",
            "pagedown": "Next",
        }
        normalized = []
        for part in parts:
            mapped = mapping.get(part.lower(), part)
            if len(mapped) == 1 and mapped.isalpha():
                mapped = mapped.lower()
            normalized.append(mapped)
        return f"<{'-'.join(normalized)}>"

    def _capture_hotkey_keypress(self, event: tk.Event, target_var: tk.StringVar):
        if event.keysym in {"Tab", "ISO_Left_Tab"}:
            return None

        if event.keysym in {"BackSpace", "Delete"}:
            target_var.set("")
            return "break"

        if event.keysym in {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}:
            return "break"

        modifiers = []
        if event.state & 0x0004:
            modifiers.append("Ctrl")
        if event.state & 0x0008:
            modifiers.append("Alt")
        if event.state & 0x0001:
            modifiers.append("Shift")

        key_mapping = {
            "Return": "Enter",
            "Escape": "Esc",
            "space": "Space",
            "Prior": "PageUp",
            "Next": "PageDown",
        }
        key_name = key_mapping.get(event.keysym, event.keysym)
        if len(key_name) == 1:
            key_name = key_name.upper()

        target_var.set("+".join([*modifiers, key_name]) if modifiers else key_name)
        return "break"

    def _capture_hotkey_dialog(self, parent: tk.Misc, title: str, initial_value: str = "") -> str | None:
        captured = {"value": (initial_value or "").strip(), "accepted": False}

        dialog = tk.Toplevel(parent)
        dialog.title(title)
        dialog.resizable(False, False)
        self._prepare_dialog(dialog, parent)

        shell = ttk.Frame(dialog, padding=12)
        shell.pack(fill="both", expand=True)

        ttk.Label(shell, text="Press a key combo, then click OK.", foreground="#4b5563").pack(anchor="w")
        value_var = tk.StringVar(value=captured["value"])
        entry = ttk.Entry(shell, textvariable=value_var, width=28)
        entry.pack(fill="x", pady=(8, 8))

        def on_keypress(event: tk.Event):
            if event.keysym in {"Tab", "ISO_Left_Tab"}:
                return None
            if event.keysym in {"BackSpace", "Delete"}:
                value_var.set("")
                return "break"
            if event.keysym in {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}:
                return "break"

            modifiers = []
            if event.state & 0x0004:
                modifiers.append("Ctrl")
            if event.state & 0x0008:
                modifiers.append("Alt")
            if event.state & 0x0001:
                modifiers.append("Shift")

            key_mapping = {
                "Return": "Enter",
                "Escape": "Esc",
                "space": "Space",
                "Prior": "PageUp",
                "Next": "PageDown",
            }
            key_name = key_mapping.get(event.keysym, event.keysym)
            if len(key_name) == 1:
                key_name = key_name.upper()

            value_var.set("+".join([*modifiers, key_name]) if modifiers else key_name)
            return "break"

        entry.bind("<KeyPress>", on_keypress)
        entry.focus_set()

        buttons = ttk.Frame(shell)
        buttons.pack(fill="x")

        def accept():
            captured["value"] = (value_var.get() or "").strip()
            captured["accepted"] = True
            dialog.destroy()

        ttk.Button(buttons, text="OK", command=accept).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")

        self._center_window(dialog, self.root)
        dialog.wait_window()
        return captured["value"] if captured["accepted"] else None

    def send_command(self, device_name: str, action_name: str):
        device = self.device_library.get(device_name)
        if not device or action_name not in device.commands:
            self.status_var.set(f"Unknown command: {device_name} / {action_name}")
            return

        configured_port = self.config_manager.get_serial_port()
        command_hex = device.commands[action_name]
        self.status_var.set(f"Sending {device_name}: {action_name}...")

        def _do_send():
            try:
                port = send_ir_command(command_hex, port=configured_port or None)
                self.root.after(0, lambda: (
                    self.status_var.set(f"Sent {device_name}: {action_name} via {port}"),
                    self.set_connection_state(True, port),
                ))
            except IRSendError as exc:
                msg = str(exc)
                self.root.after(0, lambda: (
                    self.status_var.set(f"Error: {msg}"),
                    self.set_connection_state(False, "Send failed"),
                    messagebox.showerror("Send failed", f"Unable to send the command.\n{msg}"),
                ))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: (
                    self.status_var.set(f"Error: {msg}"),
                    self.set_connection_state(False, "Send failed"),
                    messagebox.showerror("Send failed", f"Unexpected error.\n{msg}"),
                ))

        threading.Thread(target=_do_send, daemon=True).start()

    def minimize_to_tray(self):
        """Minimize window to tray instead of closing."""
        self.root.withdraw()
        if self.tray_icon:
            self.tray_icon.visible = True

    def show_from_tray(self):
        """Show window from tray."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus()

    def setup_tray(self):
        """Setup system tray icon."""
        if pystray is None or Image is None:
            return
        
        try:
            # Load app.ico for tray icon
            icon_path = PROGRAM_DIR / "images" / "app.ico"
            if icon_path.exists():
                icon_image = Image.open(icon_path)
            else:
                # Fallback to colored square if icon not found
                icon_image = Image.new('RGB', (64, 64), color='#2563eb')
            
            menu = pystray.Menu(
                pystray.MenuItem('Show', lambda *args: self.root.after(0, self.show_from_tray), default=True),
                pystray.MenuItem('Quit', lambda *args: self.root.after(0, self.actual_quit_app)),
            )
            
            self.tray_icon = pystray.Icon("IRRemote", icon_image, "IR Remote Sender", menu)
            
            # Run tray icon in background thread
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            # If tray setup fails, just continue without it
            pass

    def auto_connect_on_startup(self):
        """Attempt to auto-connect to Arduino on startup."""
        def _do_connect():
            try:
                configured_port = self.config_manager.get_serial_port()
                selected_port = None if (configured_port or "").lower() == "auto" else configured_port
                used_port, response = test_arduino_connection(port=selected_port)
                self.root.after(0, lambda: (
                    self.status_var.set(f"Connected to Arduino Compatible Device on {used_port}"),
                    self.set_connection_state(True, used_port),
                ))
            except Exception:
                # Connection failed, but that's okay - user can test manually later
                self.root.after(0, lambda: self.set_connection_state(False, "No response"))

        threading.Thread(target=_do_connect, daemon=True).start()

    def actual_quit_app(self):
        """Actually quit the application."""
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.quit_app()

    def quit_app(self):
        if self.global_hotkey_listener is not None:
            try:
                self.global_hotkey_listener.stop()
            except Exception:
                pass
            self.global_hotkey_listener = None
        self.global_hotkey_handles.clear()
        self.config_manager.set_show_overlays(self.show_overlays_var.get())
        self.save_hotkeys()
        self.config_manager.set_enabled_devices(self.enabled_devices)
        self.config_manager.save()
        self.root.destroy()
