import sys
import tkinter as tk
from pathlib import Path

from remote_app import IRRemoteApp


def main():
    root = tk.Tk()

    # Set window icon
    base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    icon_path = base_dir / "images" / "app.ico"
    if icon_path.exists():
        root.iconbitmap(str(icon_path))

    app = IRRemoteApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
