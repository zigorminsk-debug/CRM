#!/usr/bin/env python3
"""Запуск сервера CRM.

    python3 run.py                    # окно управления (порт, старт/стоп, журнал)
    python3 run.py --no-gui           # консольный режим, порт 8000
    python3 run.py --no-gui --port 9000
    python3 run.py 9000               # порт аргументом

Тот же файл — точка входа для собранного CRM-Server.exe (PyInstaller):
двойной щелчок открывает окно управления (порт, «Запустить», «Открыть
в браузере»); консольный режим — флаг --no-gui (для CI и служб).
"""
import argparse
import multiprocessing
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402

from app import _runtime  # noqa: E402
from app._runtime import local_ips  # noqa: E402

# Журнал uvicorn для консольного режима — без ANSI-цветов: классическая консоль
# Windows (conhost без VT) показывает их мусором вида «←[32mINFO←[0m».
LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(client_addr)s - "%(request_line)s" %(status_code)s',
            "use_colors": False,
        },
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}


def _console_tweaks() -> None:
    """Windows: выключаем QuickEdit у консоли собранного exe.

    QuickEdit — случайный клик мышью по окну включает выделение текста
    (в заголовке появляется «Выбрать») и замораживает вывод процесса:
    сервер висит и не отвечает, хотя запущен. Для окна сервера это вредно.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE = 0x0040
            ENABLE_EXTENDED_FLAGS = 0x0080
            new_mode = ((mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS) & 0xFFFFFFFF
            kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


def _print_addresses(host: str, port: int) -> None:
    """Понятная стартовая строка: куда заходить браузером (0.0.0.0 в браузере не открывается)."""
    if host != "0.0.0.0":
        print(f"Cartridge Engineer: http://{host}:{port}  (админка /admin, инженер /m, docs /docs)")
        return
    print(f"Cartridge Engineer сервер запущен, порт {port}. Открывайте в браузере:")
    print(f"  на этом компьютере:   http://127.0.0.1:{port}")
    ips = local_ips()
    if ips:
        for ip in ips:
            print(f"  с телефонов и других ПК: http://{ip}:{port}")
    else:
        print("  с других устройств:   http://<IP этого компьютера>:" f"{port}  (узнать: ipconfig)")
    print("  разделы: /admin — админка, /m — приложение инженера, /docs — описание API")
    if getattr(sys, "frozen", False) and os.name == "nt":
        print("Если с другого устройства адрес не открывается — разрешите порт "
              f"{port} во входящих правилах брандмауэра Windows.")


def _can_bind(host: str, port: int) -> str | None:
    """Свободен ли порт: возвращает текст ошибки или None, если можно запускаться."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return None
    except OSError as e:
        return str(e)
    finally:
        probe.close()


def _pause_if_frozen() -> None:
    """В консоли ждём Enter; в оконном exe (stdin отсутствует) молча выходим."""
    if getattr(sys, "frozen", False) and getattr(sys, "stdin", None) is not None:
        try:
            input("Нажмите Enter для выхода...")
        except (EOFError, OSError, RuntimeError):
            pass


def _is_windowed_app() -> bool:
    """Оконный exe без консоли: PyInstaller не создаёт stdin — input() недоступен."""
    return getattr(sys, "frozen", False) and getattr(sys, "stdin", None) is None


def _msgbox(title: str, text: str) -> None:
    """Диалог Windows — единственный видимый канал сообщений оконного exe."""
    try:
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x00000040)  # MB_ICONINFORMATION
    except Exception:
        pass


def _fatal(message: str, title: str = "CRM — сервер заявок") -> None:
    """Показать ошибку там, где её реально увидят: диалог в оконном exe, консоль иначе."""
    saved = _write_crash_log(message)
    if saved:
        message += f"\n\nОписание ошибки сохранено в файл:\n{saved}"
    if _is_windowed_app():
        _msgbox(title, message)
    else:
        print(message)
        _pause_if_frozen()


def _write_crash_log(text: str) -> str | None:
    """Пытаемся сохранить ошибку в файл рядом с exe — вдруг окно всё же закроется."""
    try:
        base = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.getcwd()
        path = os.path.join(base, "crm-server-error.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path
    except OSError:
        return None


def run_console(host: str, port: int) -> None:
    """Консольный режим: печать адресов и запуск uvicorn в текущем потоке."""
    # Порт проверяем ДО запуска: занятый порт — самая частая причина
    # «окно мигнуло и закрылось» (uvicorn завершается с SystemExit).
    busy = _can_bind(host, port)
    if busy:
        _fatal(f"Не удалось занять порт {port}: {busy}\n\n"
               f"Порт {port} уже занят — возможно, CRM-Server.exe уже запущен.\n"
               "Закройте старое окно сервера или снимите задачу:\n"
               "    taskkill /F /IM CRM-Server.exe\n\n"
               "Либо запустите на другом порту:\n"
               "    set PORT=8010 && CRM-Server.exe   →  http://127.0.0.1:8010")
        raise SystemExit(1)

    _print_addresses(host, port)
    try:
        if getattr(sys, "frozen", False):
            from app.main import app          # в exe модуль уже вшит — импортируем напрямую
            uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"),
                        log_config=LOG_CONFIG)
        else:
            uvicorn.run("app.main:app", host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info"),
                        log_config=LOG_CONFIG)
    except PermissionError as e:
        # CRM-Server.exe лежит в папке без прав на запись (например, C:\Program Files):
        # рядом с собой он не может создать базу данных и папку раздачи.
        _fatal("Не хватает прав записи в папку приложения:\n"
               f"{e}\n\nПеренесите CRM-Server.exe в папку с правами записи (например, C:\\CRM)\n"
               "или укажите путь к базе переменной окружения CRM_DB.")
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nСервер остановлен (Ctrl+C).")
    except SystemExit as e:
        if e.code not in (0, None):
            # сюда попадает и sys.exit(1) самого uvicorn (например, порт успели занять)
            print(f"\nСервер остановлен с кодом {e.code}.")
            print("Частые причины: порт занят другим процессом (запустите с set PORT=8010)")
            print("или нет прав записи в папке (перенесите exe, например, в C:\\CRM).")
            _pause_if_frozen()
        raise
    except BaseException:
        import traceback
        trace = traceback.format_exc()
        print(f"\nНепредвиденная ошибка:\n{trace}")
        saved = _write_crash_log(trace)
        if saved:
            print(f"Описание ошибки сохранено в файл: {saved}")
        _pause_if_frozen()
        raise SystemExit(1)
    print("Сервер остановлен.")
    _pause_if_frozen()


def run_gui(host: str, port: int) -> bool:
    """Окно управления (tkinter). False — GUI недоступен, нужен консольный режим."""
    try:
        import launcher
        return launcher.run(host, port)
    except SystemExit:
        raise
    except ImportError as e:
        # нет tkinter/дисплея — переходим в консоль, но причину показываем обязательно
        msg = (f"Окно управления недоступно ({e.__class__.__name__}: {e}).\n\n"
               "Сервер будет запущен в фоновом режиме без окна.\n"
               f"Адрес для входа: http://127.0.0.1:{port}\n"
               "Адреса для телефонов — в сообщении о запуске (консоль/журнал).")
        _fatal(msg, "Cartridge Engineer — окно управления")
        return False
    except BaseException:  # прочие сбои окна — показываем причину и уходим в консоль
        import traceback
        _fatal("Окно управления упало с ошибкой:\n" + traceback.format_exc(),
               "Cartridge Engineer — окно управления")
        return False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="CRM-Server",
        description="Сервер CRM: окно управления по умолчанию, консольный режим — --no-gui.",
    )
    parser.add_argument("port_positional", nargs="?", type=int, default=None,
                        help="порт (краткая форма: CRM-Server.exe 8010)")
    parser.add_argument("--port", type=int, default=None, help="порт сервера (по умолчанию 8000)")
    parser.add_argument("--host", default=None, help="интерфейс (по умолчанию 0.0.0.0 — все)")
    parser.add_argument("--no-gui", action="store_true",
                        help="без окна управления: консольный режим (для CI и служб)")
    try:
        args, _unknown = parser.parse_known_args()
    except SystemExit:
        args, _unknown = parser.parse_known_args([])
    return args


def main() -> None:
    host, port, no_gui = _parse_and_setup()
    if not no_gui and run_gui(host, port):
        raise SystemExit(0)
    run_console(host, port)


def _parse_and_setup() -> tuple[str, int, bool]:
    args = _parse_args()
    host = args.host or os.environ.get("HOST", "0.0.0.0")
    port = args.port or args.port_positional
    if port is None:
        env_port = os.environ.get("PORT", "").strip()
        port = int(env_port) if env_port.isdigit() else 8000
    port = int(port)
    no_gui = args.no_gui or os.environ.get("CRM_NO_GUI", "").strip().lower() in ("1", "true", "yes")
    return host, port, no_gui


if __name__ == "__main__":
    multiprocessing.freeze_support()          # корректный запуск собранного exe в Windows
    # Windows: вывод exe часто перенаправлен в файл/консоль с однобайтовой кодировкой
    # (cp1252/cp866) — кириллица в сообщениях уронит print с UnicodeEncodeError.
    # Принудительно переводим потоки вывода на UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass
    _console_tweaks()
    main()
