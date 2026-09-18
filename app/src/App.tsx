/** Root component of the desktop app: holds the batch workspace behind the
 * worker's startup state. */

import { BatchWorkspace } from "./features/batch/components/BatchWorkspace";
import { StartupScreen } from "./features/startup/StartupScreen";
import { useWorkerStartup } from "./features/startup/useWorkerStartup";

export function App() {
  const { ready, error } = useWorkerStartup();

  return <main>{ready ? <BatchWorkspace /> : <StartupScreen error={error} />}</main>;
}
