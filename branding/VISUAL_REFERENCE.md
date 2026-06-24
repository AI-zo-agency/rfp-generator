# Zo Agency Visual Reference Guide

Quick visual reference for developers implementing Zo Agency branding.

## 🎨 Color Swatches

```
PRIMARY COLORS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
███████  Black      #000000  Main text, headers
███████  White      #FFFFFF  Backgrounds, light text
███████  Cream      #F5F3EF  Soft backgrounds

ACCENT COLORS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
███████  Orange     #FF6B35  Primary CTA, highlights
███████  Coral      #FF8B6A  Secondary accent
███████  Warm Gray  #E8E4DF  Subtle backgrounds

SEMANTIC COLORS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
███████  Success    #4CAF50  Positive states
███████  Warning    #FFC107  Caution states
███████  Error      #F44336  Error states
███████  Info       #2196F3  Informational
```

## 📐 Typography Scale

```
HEADINGS (Space Grotesk, Bold)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Hero      72px   Bold   -0.02em tracking
H1        48px   Bold   -0.02em tracking
H2        36px   Bold   -0.01em tracking
H3        24px   Bold   normal tracking

BODY TEXT (Inter)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Large     18px   Regular   1.6 line-height
Normal    16px   Regular   1.6 line-height
Small     14px   Regular   1.5 line-height
Caption   12px   Regular   1.5 line-height
```

## 🔲 Component Examples

### Button Styles
```
┌─────────────────────┐
│   PRIMARY BUTTON    │  Black bg (#000000)
└─────────────────────┘  Hover: Orange (#FF6B35)
                         Font: Bold, Uppercase

┌─────────────────────┐
│   ACCENT BUTTON     │  Orange bg (#FF6B35)
└─────────────────────┘  Hover: Coral (#FF8B6A)
                         Font: Bold, Uppercase

┌─────────────────────┐
│   OUTLINE BUTTON    │  Transparent bg
└─────────────────────┘  Black border (2px)
                         Hover: Black bg, White text
```

### Card Layouts
```
╔═══════════════════════════╗
║  Stat Card                ║  White bg
║  ────────────            ║  Left border accent
║  Label (gray, uppercase) ║  Rounded corners (12px)
║  42  (large, bold)       ║  Subtle shadow
║  Subtitle (small)        ║  Hover: lift + shadow
╚═══════════════════════════╝

╔═══════════════════════════╗
║  Dark Card (Black bg)     ║  Black bg (#000000)
║  ────────────────         ║  White text
║  Content in white         ║  Used for headers
╚═══════════════════════════╝
```

### Badges
```
┌─────────┐  Default      Gray bg (#E8E4DF)
│ PENDING │  Bold, Uppercase, Rounded
└─────────┘

┌─────────┐  Accent       Orange bg (#FF6B35)
│   GO    │  White text, Bold
└─────────┘

┌─────────┐  Success      Light green bg
│ COMPLETE│  Green text (#4CAF50)
└─────────┘
```

## 📏 Spacing System

```
Base unit: 4px (0.25rem)

0  = 0px
1  = 4px     Small gaps, icon spacing
2  = 8px     Tight spacing
3  = 12px    Default gap
4  = 16px    Standard padding
6  = 24px    Card padding
8  = 32px    Section spacing
12 = 48px    Large section gaps
16 = 64px    Hero spacing
```

## 🎯 Layout Patterns

### Dashboard Header
```
║│ Dashboard                    <- Border-left (4px black)
║  RFP response automation...   <- Large heading (4xl)
                                 <- Descriptive text (base)
```

### Stat Card Grid
```
┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
│ Stat │  │ Stat │  │ Stat │  │ Stat │
└──────┘  └──────┘  └──────┘  └──────┘
  4 columns on large screens
  2 columns on tablets
  1 column on mobile
```

### Data Table
```
╔═════════════════════════════════════╗
║ Table Header (Black bg, White text)║
╠═════════════════════════════════════╣
║ Column Headers (Cream bg, Bold)    ║
╟─────────────────────────────────────╢
║ Row 1                               ║
║ Row 2   (Hover: light gray bg)     ║
║ Row 3                               ║
╚═════════════════════════════════════╝
```

## 🎨 Design Tokens Quick Copy

```css
/* Colors */
--zo-black: #000000;
--zo-white: #FFFFFF;
--zo-cream: #F5F3EF;
--zo-orange: #FF6B35;
--zo-coral: #FF8B6A;

/* Typography */
--zo-font-heading: 'Space Grotesk', sans-serif;
--zo-font-body: 'Inter', sans-serif;

/* Spacing */
--zo-space-sm: 8px;
--zo-space-md: 16px;
--zo-space-lg: 24px;
--zo-space-xl: 48px;

/* Borders */
--zo-radius: 12px;
--zo-border-width: 2px;

/* Shadows */
--zo-shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.1);
--zo-shadow-md: 0 4px 12px rgba(0, 0, 0, 0.1);
```

## 🚀 Common Patterns

### Bold Statement Section
```javascript
<div className="border-l-4 border-black pl-6">
  <h2 className="text-4xl font-bold zo-heading">
    Bold Statement
  </h2>
  <p className="mt-2 text-gray-600">
    Supporting text
  </p>
</div>
```

### Call-to-Action Button
```javascript
<button className="zo-button-primary">
  GET STARTED
</button>
```

### Stat Display
```javascript
<div className="zo-card border-l-4" 
     style={{ borderLeftColor: '#FF6B35' }}>
  <p className="text-sm uppercase">Label</p>
  <p className="text-4xl font-bold zo-heading">42</p>
  <p className="text-sm text-gray-500">Subtitle</p>
</div>
```

---

**Remember**: Bold typography, strategic color use, generous whitespace, and strong hierarchy are key to the Zo Agency aesthetic.
