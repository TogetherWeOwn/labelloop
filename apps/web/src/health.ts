export async function fetchHealth(signal: AbortSignal): Promise<string> {
  const response = await fetch('/api/healthz', { signal });
  if (!response.ok) throw new Error('API unavailable');
  let health: { status: string };
  try {
    health = (await response.json()) as { status: string };
  } catch {
    throw new Error('API unavailable');
  }
  return health.status;
}
