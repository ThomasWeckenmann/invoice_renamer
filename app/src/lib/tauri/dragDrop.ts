/** Window-level drag-and-drop with real filesystem paths. Tauri intercepts
 * native OS drag-drop before it reaches the DOM, so a plain HTML5 `drop`
 * handler never sees dropped files - this is the webview event that does. */

import { getCurrentWebview } from "@tauri-apps/api/webview";
import type { UnlistenFn } from "@tauri-apps/api/event";

export interface DragDropCallbacks {
  onHoverChange: (isOver: boolean) => void;
  onDrop: (paths: string[]) => void;
}

export function subscribeToDragDrop({ onHoverChange, onDrop }: DragDropCallbacks): Promise<UnlistenFn> {
  return getCurrentWebview().onDragDropEvent((event) => {
    switch (event.payload.type) {
      case "enter":
      case "over":
        onHoverChange(true);
        break;
      case "leave":
        onHoverChange(false);
        break;
      case "drop":
        onHoverChange(false);
        onDrop(event.payload.paths);
        break;
    }
  });
}
