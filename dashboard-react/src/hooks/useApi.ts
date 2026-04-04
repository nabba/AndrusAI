import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../api/client';

export function useApi<T>(
  path: string | null,
  interval = 0
): { data: T | null; loading: boolean; error: string | null; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pathRef = useRef(path);
  pathRef.current = path;

  const fetchData = useCallback(async () => {
    if (!pathRef.current) {
      setLoading(false);
      return;
    }
    try {
      const result = await api<T>(pathRef.current);
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    fetchData();

    if (interval > 0) {
      const timer = setInterval(fetchData, interval);
      return () => clearInterval(timer);
    }
  }, [fetchData, path, interval]);

  return { data, loading, error, refetch: fetchData };
}
