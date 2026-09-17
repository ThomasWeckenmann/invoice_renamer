/** Root component of the desktop app: hosts the batch workspace. */

import { BatchWorkspace } from "./features/batch/components/BatchWorkspace";

export function App() {
  return (
    <main>
      <BatchWorkspace />
    </main>
  );
}
