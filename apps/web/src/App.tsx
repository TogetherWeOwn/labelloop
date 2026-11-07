import { useEffect, useState } from 'react';
import './styles.css';

export function App() {
  const [apiState, setApiState] = useState('checking');

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/healthz', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('API unavailable');
        return response.json() as Promise<{ status: string }>;
      })
      .then((health) => setApiState(health.status))
      .catch(() => setApiState('starting'));
    return () => controller.abort();
  }, []);

  return (
    <main>
      <p className="eyebrow">Development workspace</p>
      <h1>Labelloop</h1>
      <p>Multi-domain image annotation with reproducible dataset compilation.</p>
      <p role="status">API status: {apiState}</p>
      <footer>
        <a href="https://github.com/TogetherWeOwn/labelloop">AGPL-3.0 source code</a>
      </footer>
    </main>
  );
}
