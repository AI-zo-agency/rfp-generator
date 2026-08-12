"use client";

import { useEffect, useRef, useState } from "react";

const easeOutExpo = (t: number) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t));

interface AnimatedNumberProps {
  value: number;
  decimals?: number;
  duration?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
  /** Render the in-flight value. Defaults to `toFixed(decimals)`; pass a
   * locale formatter when the number is money and needs group separators. */
  format?: (value: number) => string;
}

/** Counts up from its previous value to `value` whenever it changes — the one
 * authored moment for this dashboard's headline stats materializing. */
export function AnimatedNumber({
  value,
  decimals = 0,
  duration = 800,
  prefix = "",
  suffix = "",
  className,
  format,
}: AnimatedNumberProps) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(0);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    const from = fromRef.current;
    const to = value;
    const start = performance.now();

    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);

    const tick = (now: number) => {
      const elapsed = now - start;
      const t = Math.min(1, elapsed / duration);
      const eased = easeOutExpo(t);
      setDisplay(from + (to - from) * eased);
      if (t < 1) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        fromRef.current = to;
      }
    };

    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, duration]);

  return (
    <span className={className}>
      {prefix}
      {format ? format(display) : display.toFixed(decimals)}
      {suffix}
    </span>
  );
}
