const getDefaultApiBaseUrl = () => {
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    if (host === 'localhost' || host === '127.0.0.1') {
      return 'http://localhost:8000';
    }
  }
  return 'https://quantiative-trading-api.onrender.com';
};

const rawBaseUrl = import.meta.env.VITE_API_BASE_URL ?? getDefaultApiBaseUrl();

export const API_BASE_URL = rawBaseUrl.replace(/\/+$/, '');

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}