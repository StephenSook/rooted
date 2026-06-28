"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

// Mount a heavy (WebGL) child only once its container scrolls near the viewport, so an off-screen
// canvas does not spin up a GL context and animate while the user is reading elsewhere. Once seen it
// stays mounted (no remount churn). rootMargin pre-mounts it just before it scrolls into view, so the
// 3D graph is ready by the time the user reaches it. SSR and browsers without IntersectionObserver
// fall back to mounting immediately, so the content is never withheld.
export function useInView<T extends Element>(
  rootMargin = "200px",
): [RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (inView) return;
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setInView(true);
      },
      { rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [inView, rootMargin]);

  return [ref, inView];
}
