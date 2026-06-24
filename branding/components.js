/**
 * Zo Agency Reusable Component Styles
 * Common component patterns following Zo Agency design system
 */

export const zoComponents = {
  // Button variants
  buttons: {
    primary: {
      backgroundColor: '#000000',
      color: '#FFFFFF',
      padding: '12px 24px',
      borderRadius: '8px',
      fontSize: '14px',
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      transition: 'all 200ms ease-in-out',
      border: 'none',
      cursor: 'pointer',
      hover: {
        backgroundColor: '#FF6B35',
        transform: 'translateY(-2px)',
        boxShadow: '0 4px 12px rgba(255, 107, 53, 0.3)',
      },
    },
    accent: {
      backgroundColor: '#FF6B35',
      color: '#FFFFFF',
      padding: '12px 24px',
      borderRadius: '8px',
      fontSize: '14px',
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      transition: 'all 200ms ease-in-out',
      border: 'none',
      cursor: 'pointer',
      hover: {
        backgroundColor: '#FF8B6A',
        transform: 'translateY(-2px)',
        boxShadow: '0 4px 12px rgba(255, 107, 53, 0.4)',
      },
    },
    outline: {
      backgroundColor: 'transparent',
      color: '#000000',
      padding: '12px 24px',
      borderRadius: '8px',
      fontSize: '14px',
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      transition: 'all 200ms ease-in-out',
      border: '2px solid #000000',
      cursor: 'pointer',
      hover: {
        backgroundColor: '#000000',
        color: '#FFFFFF',
      },
    },
  },

  // Card styles
  cards: {
    default: {
      backgroundColor: '#FFFFFF',
      borderRadius: '12px',
      padding: '24px',
      boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)',
      transition: 'all 200ms ease-in-out',
      hover: {
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
        transform: 'translateY(-2px)',
      },
    },
    highlighted: {
      backgroundColor: '#FFFFFF',
      borderRadius: '12px',
      padding: '24px',
      borderLeft: '4px solid #FF6B35',
      boxShadow: '0 1px 3px rgba(0, 0, 0, 0.1)',
    },
    dark: {
      backgroundColor: '#000000',
      color: '#FFFFFF',
      borderRadius: '12px',
      padding: '24px',
      boxShadow: '0 4px 12px rgba(0, 0, 0, 0.2)',
    },
  },

  // Input styles
  inputs: {
    text: {
      backgroundColor: '#FFFFFF',
      border: '2px solid #E0E0E0',
      borderRadius: '8px',
      padding: '12px 16px',
      fontSize: '16px',
      color: '#000000',
      transition: 'all 200ms ease-in-out',
      focus: {
        borderColor: '#FF6B35',
        outline: 'none',
        boxShadow: '0 0 0 3px rgba(255, 107, 53, 0.1)',
      },
    },
  },

  // Badge styles
  badges: {
    default: {
      backgroundColor: '#E8E4DF',
      color: '#000000',
      padding: '6px 16px',
      borderRadius: '20px',
      fontSize: '12px',
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
    },
    accent: {
      backgroundColor: '#FF6B35',
      color: '#FFFFFF',
      padding: '6px 16px',
      borderRadius: '20px',
      fontSize: '12px',
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
    },
    success: {
      backgroundColor: 'rgba(76, 175, 80, 0.15)',
      color: '#4CAF50',
      padding: '6px 16px',
      borderRadius: '20px',
      fontSize: '12px',
      fontWeight: '700',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
    },
  },

  // Typography presets
  typography: {
    hero: {
      fontFamily: 'Space Grotesk, sans-serif',
      fontSize: '72px',
      fontWeight: '700',
      lineHeight: '1.1',
      letterSpacing: '-0.02em',
      color: '#000000',
    },
    heading1: {
      fontFamily: 'Space Grotesk, sans-serif',
      fontSize: '48px',
      fontWeight: '700',
      lineHeight: '1.2',
      letterSpacing: '-0.02em',
      color: '#000000',
    },
    heading2: {
      fontFamily: 'Space Grotesk, sans-serif',
      fontSize: '36px',
      fontWeight: '700',
      lineHeight: '1.3',
      letterSpacing: '-0.01em',
      color: '#000000',
    },
    heading3: {
      fontFamily: 'Space Grotesk, sans-serif',
      fontSize: '24px',
      fontWeight: '700',
      lineHeight: '1.4',
      color: '#000000',
    },
    body: {
      fontFamily: 'Inter, sans-serif',
      fontSize: '16px',
      fontWeight: '400',
      lineHeight: '1.6',
      color: '#000000',
    },
    bodyLarge: {
      fontFamily: 'Inter, sans-serif',
      fontSize: '18px',
      fontWeight: '400',
      lineHeight: '1.6',
      color: '#000000',
    },
    caption: {
      fontFamily: 'Inter, sans-serif',
      fontSize: '14px',
      fontWeight: '400',
      lineHeight: '1.5',
      color: '#6B6B6B',
    },
  },
};

export default zoComponents;
