/** Root component of the desktop app: hosts the batch workspace. */

import { BatchWorkspace } from "./features/batch/components/BatchWorkspace";

export function App() {
  return (
    <main>
      <h1>Invoice Renamer</h1>
      <BatchWorkspace />
    </main>
  );
}
