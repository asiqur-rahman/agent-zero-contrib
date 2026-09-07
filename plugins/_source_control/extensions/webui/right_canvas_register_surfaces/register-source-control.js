import { store as sourceControlStore } from "/plugins/_source_control/webui/source-control-store.js";

function waitForElement(selector, timeoutMs = 3000) {
  const found = document.querySelector(selector);
  if (found) return Promise.resolve(found);
  return new Promise((resolve) => {
    const timeout = globalThis.setTimeout(() => {
      observer.disconnect();
      resolve(document.querySelector(selector));
    }, timeoutMs);
    const observer = new MutationObserver(() => {
      const element = document.querySelector(selector);
      if (!element) return;
      globalThis.clearTimeout(timeout);
      observer.disconnect();
      resolve(element);
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

export default async function registerSourceControlSurface(surfaces) {
  surfaces.registerSurface({
    id: "source-control",
    title: "Source Control",
    icon: "commit",
    order: 40,
    modalPath: "/plugins/_source_control/webui/main.html",
    async open(payload = {}) {
      const panel = await waitForElement('[data-surface-id="source-control"] .sc-panel');
      if (!panel) throw new Error("Source Control surface panel did not mount.");
      await sourceControlStore.onOpen?.(payload);
    },
    async close() {
      sourceControlStore.cleanup?.();
    },
  });
}
