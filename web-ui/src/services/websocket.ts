type WebSocketEventHandler = (data: unknown) => void;

const MAX_RECONNECT_ATTEMPTS = 20;

function resolveWsUrl(): string {
  const envUrl = (import.meta as any).env?.VITE_WS_URL as string | undefined;
  if (envUrl) return envUrl;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  return `${protocol}//${host}/ws`;
}

class WebSocketService {
  private ws: WebSocket | null = null;
  private reconnectInterval = 3000;
  private reconnectAttempts = 0;
  private listeners: Map<string, WebSocketEventHandler[]> = new Map();
  public isConnected = false;
  private shouldConnect = false;

  constructor() {
    if (localStorage.getItem('auth_logged_in') === 'true') {
      this.connect();
    }
  }

  public start() {
    this.shouldConnect = true;
    this.reconnectAttempts = 0;
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED) {
      this.connect();
    }
  }

  public stop() {
    this.shouldConnect = false;
    this.reconnectAttempts = 0;
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private connect() {
    if (localStorage.getItem('auth_logged_in') !== 'true') return;

    const url = resolveWsUrl();
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.isConnected = true;
      this.emit('connected', { isConnected: true });
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type) {
          this.emit(message.type, message.data);
        }
      } catch {
        // ignore malformed messages
      }
    };

    this.ws.onclose = () => {
      if (this.isConnected) {
        this.isConnected = false;
        this.emit('disconnected', { isConnected: false });
      }
      if (localStorage.getItem('auth_logged_in') !== 'true') return;
      if (!this.shouldConnect && localStorage.getItem('auth_logged_in') !== 'true') return;
      if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) return;
      this.reconnectAttempts++;
      setTimeout(() => this.connect(), this.reconnectInterval);
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  public on(event: string, handler: WebSocketEventHandler) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event)?.push(handler);
  }

  public off(event: string, handler: WebSocketEventHandler) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      const index = handlers.indexOf(handler);
      if (index !== -1) {
        handlers.splice(index, 1);
      }
    }
  }

  private emit(event: string, data: unknown) {
    const handlers = this.listeners.get(event);
    if (handlers) {
      handlers.forEach((handler) => handler(data));
    }
  }
}

export const wsService = new WebSocketService();
