# Zo Agency Branding Guidelines

Brand styling and design system based on [Zo Agency](https://zo.agency/)

## Overview

Zo Agency is a full-service branding and marketing firm based in Bend, Oregon, specializing in strategy-first solutions. Their design aesthetic is clean, modern, bold, and purposeful.

## Design Philosophy

- **Bold & Minimalist**: Clean layouts with strong typography
- **Purpose-Driven**: Every element serves a clear function
- **Human-Centered**: Warm, approachable, yet professional
- **Modern**: Contemporary design with timeless appeal

## Color Palette

### Primary Colors
- **Black** (#000000) - Main text, bold statements
- **White** (#FFFFFF) - Clean backgrounds
- **Cream** (#F5F3EF) - Soft, warm backgrounds

### Accent Colors
- **Orange** (#FF6B35) - Primary CTA, highlights
- **Coral** (#FF8B6A) - Secondary accent
- **Warm Gray** (#E8E4DF) - Subtle backgrounds

## Typography

### Font Stack
- **Headings**: Space Grotesk (bold, modern geometric sans-serif)
- **Body**: Inter (clean, highly readable)
- **Monospace**: Roboto Mono (technical content)

### Type Scale
- Display/Hero: 60-72px
- H1: 48px
- H2: 36px
- H3: 30px
- H4: 24px
- Body: 16px
- Small: 14px

## Usage Guidelines

### Import in Your Components

```javascript
import { zoColors } from './branding/colors';
import { zoTypography } from './branding/typography';
import { zoTheme } from './branding/theme';
```

### Using Colors

```javascript
// In styled components
color: ${zoColors.primary.black};
background: ${zoColors.accent.orange};

// In inline styles
style={{ color: zoColors.text.primary }}
```

### Using Typography

```javascript
// Font families
fontFamily: ${zoTypography.fonts.heading};

// Font sizes
fontSize: ${zoTypography.sizes['4xl']};

// Font weights
fontWeight: ${zoTypography.weights.bold};
```

## Design Principles

1. **Clarity First**: Clear communication over decoration
2. **Bold Typography**: Let text make a statement
3. **Generous Whitespace**: Let content breathe
4. **Strategic Color**: Use accent colors purposefully
5. **Consistency**: Maintain visual rhythm throughout

## Key Brand Elements

- **Tagline Style**: Short, punchy phrases in uppercase
- **Hero Text**: Large, bold statements that capture attention
- **Button Style**: Solid fills with clear contrast
- **Card Design**: Clean, minimal with subtle shadows
- **Navigation**: Simple, clear, uncluttered

## Contact

Zo Agency
- Website: https://zo.agency/
- Phone: (541) 350-2778
- Email: connect@zo.agency
- Location: Bend, Oregon

---

*This branding system is for reference and implementation within this project.*
