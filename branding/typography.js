/**
 * Zo Agency Typography System
 * Source: https://zo.agency/
 *
 * App UI uses Open Sans + Cabin (see frontend layout).
 * Proposal writing rules (not fonts) live in
 * branding/ZO_BRAND_AND_WRITING_STANDARDS_REV3.md.
 */

export const zoTypography = {
  // Font Families
  fonts: {
    primary: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    heading: '"Space Grotesk", "Inter", sans-serif',
    display: '"Space Grotesk", sans-serif',
    body: '"Inter", sans-serif',
    mono: '"Roboto Mono", "Courier New", monospace',
  },

  // Font Sizes
  sizes: {
    xs: '0.75rem',      // 12px
    sm: '0.875rem',     // 14px
    base: '1rem',       // 16px
    lg: '1.125rem',     // 18px
    xl: '1.25rem',      // 20px
    '2xl': '1.5rem',    // 24px
    '3xl': '1.875rem',  // 30px
    '4xl': '2.25rem',   // 36px
    '5xl': '3rem',      // 48px
    '6xl': '3.75rem',   // 60px
    '7xl': '4.5rem',    // 72px
  },

  // Font Weights
  weights: {
    light: 300,
    normal: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
    extrabold: 800,
  },

  // Line Heights
  lineHeights: {
    tight: 1.2,
    snug: 1.375,
    normal: 1.5,
    relaxed: 1.625,
    loose: 2,
  },

  // Letter Spacing
  letterSpacing: {
    tighter: '-0.05em',
    tight: '-0.025em',
    normal: '0',
    wide: '0.025em',
    wider: '0.05em',
    widest: '0.1em',
  },
};

export default zoTypography;
