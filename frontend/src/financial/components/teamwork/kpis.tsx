"use client";

import { AnimatedNumber } from "../AnimatedNumber";

export function Count({ value }: { value: number }) {
  return (
    <AnimatedNumber
      value={value}
      format={(n) => Math.round(n).toLocaleString("en-US")}
    />
  );
}

export function HoursValue({ minutes }: { minutes: number }) {
  const hours = (minutes || 0) / 60;
  const decimals = !hours || hours >= 10 ? 0 : 1;
  return <AnimatedNumber value={hours} decimals={decimals} suffix="h" />;
}
