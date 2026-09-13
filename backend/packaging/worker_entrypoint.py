"""PyInstaller entrypoint that builds into the Tauri sidecar executable."""

from invoice_renamer.api.server import main

if __name__ == "__main__":
    main()
