# Web Design Guidelines

When reviewing or generating web interfaces, follow these standards from vercel-labs/web-interface-guidelines.

## Core Principles

### 1. Spatial Design
- Use consistent spacing scale (4px base: 4, 8, 12, 16, 24, 32, 48, 64)
- White space is a feature, not wasted space
- Content hierarchy through size, weight, and proximity — not just color

### 2. Typography
- Max 2 font families (1 for headings, 1 for body, or just 1 for both)
- Body text: 16-18px, line-height 1.5-1.6
- Headings: clear size hierarchy (1.25x ratio between levels)
- Never go below 14px for any readable text

### 3. Color
- Define semantic colors: --text, --bg, --surface, --accent, --muted, --border
- Maximum 3 accent colors (primary, success, danger)
- Contrast ratio: minimum 4.5:1 for body text (WCAG AA)
- Dark mode: don't just invert — reduce contrast slightly (90% instead of 100%)

### 4. Responsive Design
- Mobile-first: design for 320px, enhance for larger
- Breakpoints: 640px (mobile→tablet), 1024px (tablet→desktop)
- Touch targets: minimum 44x44px
- No horizontal scrolling below 1024px

### 5. Interaction Design
- Hover states on all clickable elements
- Focus indicators for keyboard navigation
- Loading states for async operations (skeleton screens > spinners)
- Transitions: 150-200ms ease for micro-interactions

### 6. Accessibility
- All images have alt text
- Form inputs have labels (not just placeholders)
- Color is never the only indicator (add icons or text)
- Skip-to-content link for keyboard users
- aria-label on icon-only buttons

### 7. Performance
- Minimize DOM depth (max 15 levels)
- CSS: prefer class selectors over complex chains
- Images: use appropriate format (WebP for photos, SVG for icons)
- Lazy-load below-fold images

## Audit Checklist (When Reviewing UI Code)

- [ ] Consistent spacing (8px grid system)
- [ ] Typography hierarchy clear (H1 > H2 > H3 > body)
- [ ] Color contrast meets WCAG AA (4.5:1 for text)
- [ ] Responsive at 320px, 640px, 1024px
- [ ] Touch targets >= 44px
- [ ] All interactive elements have hover + focus states
- [ ] Forms have proper labels and validation messages
- [ ] No text smaller than 14px
- [ ] Semantic HTML used (nav, main, section, article)
- [ ] Loading states for all async operations
