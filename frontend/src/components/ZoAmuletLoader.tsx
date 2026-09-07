"use client";

import styles from "./ZoAmuletLoader.module.css";

type ZoAmuletLoaderProps = {
  /** Accessible status for screen readers */
  label?: string;
  /** Fill the viewport (auth / shell gates) */
  fullScreen?: boolean;
  className?: string;
};

/**
 * Branded loading amulet — orange ring + zö agency wordmark.
 */
export function ZoAmuletLoader({
  label = "Loading",
  fullScreen = false,
  className = "",
}: Readonly<ZoAmuletLoaderProps>) {
  return (
    <div
      className={`${styles.zoAmulet} ${fullScreen ? styles.zoAmuletScreen : ""} ${className}`.trim()}
      role="status"
      aria-busy="true"
      aria-label={label}
    >
      <div className={styles.glow} aria-hidden />
      <div className={styles.ring} aria-hidden />
      <span className={styles.mark}>zö agency</span>
    </div>
  );
}
