"""PyInstaller entrypoint that builds into the Tauri sidecar executable."""

import os
import time

# Emitted before the application imports below, so a startup measurement can
# tell the cost of getting to Python at all (which for a onefile build includes
# unpacking the bundle to a temporary directory) apart from the worker's own
# imports and socket bind. Opt-in, so a normal launch prints nothing extra.
if os.environ.get("INVOICE_RENAMER_STARTUP_TRACE"):
    print(f"INVOICE_RENAMER_TRACE python_entry {time.time():.6f}", flush=True)

from invoice_renamer.api.server import main  # noqa: E402

if __name__ == "__main__":
    main()
