// Node 22+ ships its own (opt-in, non-functional-by-default) global
// `localStorage`, which shadows jsdom's implementation and throws instead of
// storing anything. Replace it with a plain in-memory Storage before any
// app code (which reads localStorage at module scope, e.g. the auth store)
// gets imported.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length() {
    return this.store.size;
  }
  clear() {
    this.store.clear();
  }
  getItem(key: string) {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  key(index: number) {
    return Array.from(this.store.keys())[index] ?? null;
  }
  removeItem(key: string) {
    this.store.delete(key);
  }
  setItem(key: string, value: string) {
    this.store.set(key, String(value));
  }
}

for (const target of [globalThis, window]) {
  Object.defineProperty(target, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}

import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  window.localStorage.clear();
});

// jsdom doesn't implement ResizeObserver, which Recharts' ResponsiveContainer
// reads to size charts.
if (!("ResizeObserver" in window)) {
  // @ts-expect-error -- minimal jsdom polyfill
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

vi.stubGlobal("scrollTo", vi.fn());
