const PROJECT_BOOTSTRAP_RETRY_DELAYS = [100, 250, 500] as const;

export type ProjectBootstrapPhase =
  | "unresolved"
  | "shell"
  | "authoritative"
  | "degraded";

export async function runWithBootstrapBackoff<T>(
  load: () => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    if (signal?.aborted) throw new DOMException("Bootstrap cancelled", "AbortError");
    try {
      return await load();
    } catch (error) {
      if (signal?.aborted)
        throw new DOMException("Bootstrap cancelled", "AbortError");
      const delay = PROJECT_BOOTSTRAP_RETRY_DELAYS[attempt];
      if (delay === undefined) throw error;
      await new Promise<void>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          signal?.removeEventListener("abort", onAbort);
          resolve();
        }, delay);
        const onAbort = () => {
          window.clearTimeout(timer);
          reject(new DOMException("Bootstrap cancelled", "AbortError"));
        };
        signal?.addEventListener("abort", onAbort, { once: true });
      });
    }
  }
}
