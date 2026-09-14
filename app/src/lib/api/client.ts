/** Thin fetch wrapper for the local worker: resolves the Tauri-issued session
 * endpoint, attaches the bearer token, and normalizes error responses. */

import { invoke } from "@tauri-apps/api/core";

interface WorkerEndpoint {
  port: number;
  token: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function resolveEndpoint(): Promise<WorkerEndpoint> {
  return invoke<WorkerEndpoint>("get_worker_endpoint");
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") {
        return detail;
      }
    }
  } catch {
    // Response body wasn't JSON; fall through to the generic message below.
  }
  return response.statusText || `request failed with status ${response.status}`;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { port, token } = await resolveEndpoint();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`http://127.0.0.1:${port}${path}`, { ...init, headers });
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
