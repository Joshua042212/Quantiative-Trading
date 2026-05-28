const rawBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export const API_BASE_URL = rawBaseUrl.replace(/\/+$/, '');

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}