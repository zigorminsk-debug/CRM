"""Окно управления CRM-сервером (tkinter).

Открывается при обычном запуске CRM-Server.exe: можно указать порт,
запустить и остановить сервер, открыть админку в браузере. Журнал — в окне.
Сервер стартует автоматически (порт по умолчанию 8000); если порт занят,
окно предложит вписать другой.

Если tkinter недоступен (собран без GUI), run.py уходит в консольный режим.
"""
from __future__ import annotations

import logging
import os
import queue
import socket
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk

import uvicorn

from app import _runtime

COLOR_RUN = "#0a7d26"      # зелёный
COLOR_BUSY = "#b00020"     # красный
COLOR_WAIT = "#b36b00"     # оранжевый
COLOR_OFF = "#666666"      # серый


class _QueueHandler(logging.Handler):
    """Пересылает записи журнала в очередь — окно забирает их таймером."""

    def __init__(self, out: "queue.SimpleQueue[str]") -> None:
        super().__init__()
        self._out = out
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._out.put_nowait(self.format(record))
        except Exception:  # журнал не должен ронять сервер
            pass


class ControlPanel:
    """Окно: порт, запуск/остановка, браузер, адреса и журнал."""

    def __init__(self, root: tk.Tk, host: str, port: int) -> None:
        self.root = root
        self.host = host
        self.initial_port = port
        self.q: "queue.SimpleQueue[str]" = queue.SimpleQueue()
        self.log_handler = _QueueHandler(self.q)
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.port = 0
        self.expect_running = False
        self.announced = False

        self._build()
        self._attach_logging()
        self.root.after(400, self._poll)
        self.root.after(300, self.start)   # автозапуск при открытии окна

    # ------------------------------------------------------------ интерфейс
    def _build(self) -> None:
        try:
            from app import updates
            version = updates.version_info()["version"]
        except Exception:
            version = ""
        self.root.title("Cartridge Engineer — сервер заявок" + (f" (версия {version})" if version else ""))
        try:
            icon = os.path.join(_runtime.bundle_root(), "app.ico")
            if os.path.exists(icon):
                self.root.iconbitmap(icon)
        except Exception:
            pass
        self.root.minsize(560, 380)

        top = ttk.Frame(self.root, padding=(10, 8))
        top.pack(fill="x")
        self.status = tk.Label(top, text="Остановлен", fg=COLOR_OFF, font=("", 11, "bold"))
        self.status.pack(anchor="w")

        row = ttk.Frame(top)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Порт:").pack(side="left")
        self.port_var = tk.StringVar(value=str(self.initial_port))
        self.port_entry = ttk.Entry(row, width=7, textvariable=self.port_var)
        self.port_entry.pack(side="left", padx=(4, 10))
        self.btn_start = ttk.Button(row, text="▶ Запустить", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 4))
        self.btn_stop = ttk.Button(row, text="■ Остановить", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 4))
        self.btn_browser = ttk.Button(row, text="Открыть в браузере", command=self.open_browser,
                                      state="disabled")
        self.btn_browser.pack(side="left")
        if os.name == "nt":
            self.btn_fw = ttk.Button(row, text="Разрешить в брандмауэре", command=self.allow_firewall)
            self.btn_fw.pack(side="left", padx=(4, 0))

        self.addr_var = tk.StringVar(value="")
        tk.Label(top, textvariable=self.addr_var, justify="left", fg="#333333").pack(anchor="w")

        frame = ttk.LabelFrame(self.root, text=" Журнал ", padding=4)
        frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log_text = tk.Text(frame, height=10, state="disabled", wrap="none",
                                font=("Consolas", 9))
        scroll = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._log("Cartridge Engineer — сервер заявок. Разделы: /admin — админка, /m — инженер, /docs — API.")
        self._log("Если порт занят — впишите другой (например, 8010) и нажмите «Запустить».")

    def _attach_logging(self) -> None:
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            lg.handlers = [self.log_handler]
            lg.propagate = False
            lg.setLevel(logging.INFO)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, text: str, color: str) -> None:
        self.status.configure(text=text, fg=color)

    def _toggle_buttons(self, running: bool) -> None:
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_browser.configure(state="normal" if running and self.announced else "disabled")

    # ------------------------------------------------------------- сервер
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        raw = self.port_var.get().strip()
        try:
            port = int(raw)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._set_status("Порт должен быть числом от 1 до 65535", COLOR_BUSY)
            return

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((self.host, port))
        except OSError:
            self._set_status(f"Порт {port} занят — укажите другой", COLOR_BUSY)
            self._log(f"Порт {port} занят. Впишите свободный (например, 8010) и нажмите «Запустить».")
            return
        finally:
            probe.close()

        try:
            from app.main import app   # модуль вшит в exe / лежит рядом
        except Exception:
            self._set_status("Ошибка загрузки сервера — смотрите журнал", COLOR_BUSY)
            self._log(traceback.format_exc())
            return

        config = uvicorn.Config(app, host=self.host, port=port,
                                log_level="info", log_config=None)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = port
        self.announced = False
        self.expect_running = True
        self.thread.start()
        self._set_status(f"Запускается на порту {port}…", COLOR_WAIT)
        self._toggle_buttons(running=True)

    def stop(self) -> None:
        if self.server is not None:
            self._log("Останавливаю сервер…")
            self.server.should_exit = True
            if self.thread is not None:
                self.thread.join(timeout=5)
        self.server = None
        self.thread = None
        self.expect_running = False
        self._set_status("Остановлен", COLOR_OFF)
        self._toggle_buttons(running=False)
        self.addr_var.set("")

    def open_browser(self) -> None:
        if self.announced and self.port:
            webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def allow_firewall(self) -> None:
        """Одной кнопкой открывает входящий порт в брандмауэре Windows
        (через UAC): без этого телефоны и другие компьютеры не видят сервер."""
        if os.name != "nt":
            return
        raw = self.port_var.get().strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 65535:
            self._set_status("Укажите корректный порт", COLOR_BUSY)
            return
        try:
            import ctypes

            params = (f'advfirewall firewall add rule name="CRM server {raw}" '
                      f"dir=in action=allow protocol=TCP localport={raw}")
            rc = ctypes.windll.shell32.ShellExecuteW(
                self.root.winfo_id(), "runas", "netsh", params, None, 1)
            if rc > 32:
                self._log(f"Правило брандмауэра добавлено: входящий TCP-порт {raw} разрешён. "
                          "Теперь сервер виден с телефонов и других ПК.")
            else:
                self._log("Не удалось добавить правило (отказ в запросе прав?). "
                          f"Добавьте вручную от администратора: netsh advfirewall firewall "
                          f'add rule name="CRM server {raw}" dir=in action=allow protocol=TCP localport={raw}')
        except Exception as exc:  # UAC недоступен и т.п.
            self._log(f"Не получилось открыть порт в брандмауэре: {exc}")

    def on_close(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
        self.root.destroy()

    # --------------------------------------------------------------- цикл
    def _poll(self) -> None:
        while True:
            try:
                self._log(self.q.get_nowait())
            except queue.Empty:
                break

        alive = self.thread is not None and self.thread.is_alive()
        started = bool(self.server is not None and getattr(self.server, "started", False))
        if started and not self.announced:
            self.announced = True
            self._set_status(f"Работает: http://127.0.0.1:{self.port}", COLOR_RUN)
            lines = [f"Этот компьютер:  http://127.0.0.1:{self.port}"]
            for ip in _runtime.local_ips():
                lines.append(f"Телефоны и другие ПК:  http://{ip}:{self.port}")
            lines.append("Если с других устройств не открывается — разрешите порт в брандмауэре Windows.")
            self.addr_var.set("\n".join(lines))
            self._toggle_buttons(running=True)
            self._log("Сервер запущен.")
        elif self.expect_running and not alive:
            self.expect_running = False
            self.announced = False
            self._set_status("Сервер остановился с ошибкой — смотрите журнал", COLOR_BUSY)
            self._toggle_buttons(running=False)

        self.root.after(400, self._poll)


def run(host: str, port: int) -> bool:
    """Открывает окно управления. True — окно отработало и закрылось.

    False — GUI недоступен (нет tkinter/дисплея): вызывающий код уйдёт
    в консольный режим.
    """
    panel: ControlPanel | None = None
    try:
        root = tk.Tk()
        panel = ControlPanel(root, host, port)
        root.mainloop()
        return True
    except Exception:
        traceback.print_exc()
        if panel is not None:
            try:
                panel.stop()
            except Exception:
                pass
        return False
