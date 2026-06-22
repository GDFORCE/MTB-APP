// Dawn Rounds design tokens — converted from the v0 OKLCH palette to hex/rgba
// so React Native can consume them directly. Tone-mapped for the warm cream-blush
// paper, deep plum ink, raspberry-rose primary and apricot→rose dawn gradient.
export const colors = {
  background: '#FBF2E8',        // cream-blush paper
  surface: '#F4E5D3',           // peach-cream panels
  card: '#FEFAF1',              // warm white card
  foreground: '#2E1B33',        // deep plum ink
  mutedFg: '#7B5F73',           // plum-gray AA on cream
  border: '#E6D6C5',            // warm hairline
  input: '#E6D6C5',

  primary: '#A6213F',           // raspberry-rose
  primaryDeep: '#6B1437',       // deep mulberry
  primaryFg: '#FBF2E8',
  secondary: '#F0D7DC',         // pale rose tint
  secondaryFg: '#7A1834',

  accent: '#E69B5C',            // apricot
  accentFg: '#5A3318',
  info: '#7B6BB8',              // dusty lavender
  infoFg: '#FFFFFF',
  violet: '#8E5BB4',
  warning: '#D89A3C',
  warningFg: '#FFFFFF',
  success: '#5C9A6E',
  successFg: '#FFFFFF',
  destructive: '#C0392B',
  destructiveFg: '#FFFFFF',

  dawnFrom: '#F5C57A',          // apricot
  dawnMid: '#E07A4B',           // sunrise coral
  dawnTo: '#A6213F',            // deep rose
  white: '#FFFFFF',
  black: '#000000',
  overlay10: 'rgba(255,255,255,0.10)',
  overlay20: 'rgba(255,255,255,0.20)',
  overlay25: 'rgba(255,255,255,0.25)',
};

export const dawnGradient = [colors.dawnFrom, colors.dawnMid, colors.dawnTo] as const;
export const heroGradient = [colors.primary, colors.primaryDeep] as const;

export const radii = { sm: 8, md: 12, lg: 16, xl: 24, pill: 999 };
export const spacing = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, xxl: 40 };
export const shadows = {
  sm: { shadowColor: '#2E1B33', shadowOpacity: 0.08, shadowRadius: 8, shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  md: { shadowColor: '#2E1B33', shadowOpacity: 0.12, shadowRadius: 16, shadowOffset: { width: 0, height: 6 }, elevation: 6 },
};

export const typography = {
  display: { fontSize: 32, fontWeight: '700' as const, letterSpacing: -0.6 },
  h1: { fontSize: 26, fontWeight: '700' as const, letterSpacing: -0.4 },
  h2: { fontSize: 20, fontWeight: '600' as const, letterSpacing: -0.2 },
  body: { fontSize: 15, fontWeight: '400' as const },
  small: { fontSize: 13, fontWeight: '400' as const },
  eyebrow: { fontSize: 11, fontWeight: '700' as const, letterSpacing: 1.4, textTransform: 'uppercase' as const },
};
