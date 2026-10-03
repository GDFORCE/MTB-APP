// Clinical design tokens: cool neutral surfaces, slate ink, a clinical blue
// primary and a navy for headers. Status colours are chosen to stay readable as
// text on white and as white text on a filled badge. The legacy "dawn" keys are
// kept so existing screens keep compiling; they now resolve to the blue/navy
// ramp instead of the old apricot-rose gradient.
export const colors = {
  background: '#F4F6F9',        // app canvas
  surface: '#EDF1F6',           // secondary panels, table headers
  card: '#FFFFFF',
  foreground: '#0F1B2D',        // primary text
  mutedFg: '#556275',           // secondary text, AA on white and canvas
  border: '#DDE3EA',
  input: '#C9D2DD',

  primary: '#1B5FC1',           // clinical blue
  primaryDeep: '#0D2A4F',       // navy: headers, heroes
  primaryFg: '#FFFFFF',
  secondary: '#E6EEFA',         // blue tint
  secondaryFg: '#123F80',

  accent: '#0B8074',            // teal
  accentFg: '#FFFFFF',
  info: '#2D6CB5',
  infoFg: '#FFFFFF',
  violet: '#5E4DB2',
  warning: '#B26A00',
  warningFg: '#FFFFFF',
  success: '#1E7A46',
  successFg: '#FFFFFF',
  destructive: '#C23030',
  destructiveFg: '#FFFFFF',

  dawnFrom: '#2A6FD0',
  dawnMid: '#1B5FC1',
  dawnTo: '#0D2A4F',
  white: '#FFFFFF',
  black: '#000000',
  overlay10: 'rgba(255,255,255,0.10)',
  overlay20: 'rgba(255,255,255,0.20)',
  overlay25: 'rgba(255,255,255,0.25)',
};

export const dawnGradient = [colors.dawnFrom, colors.dawnMid, colors.dawnTo] as const;
export const heroGradient = [colors.primary, colors.primaryDeep] as const;

export const radii = { sm: 6, md: 8, lg: 12, xl: 14, pill: 999 };
export const spacing = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, xxl: 40 };
export const shadows = {
  sm: { shadowColor: '#0F1B2D', shadowOpacity: 0.05, shadowRadius: 4, shadowOffset: { width: 0, height: 1 }, elevation: 1 },
  md: { shadowColor: '#0F1B2D', shadowOpacity: 0.08, shadowRadius: 12, shadowOffset: { width: 0, height: 4 }, elevation: 3 },
};

// Type families: Figtree for headings and UI, Spline Sans Mono for OTP and dense
// numerals. Loaded in app/_layout via use-app-fonts. Custom fonts embed their weight, so we swap FAMILY per weight
// (fontWeight is ignored once a custom fontFamily is set).
export const fonts = {
  display: 'Figtree-Bold',
  heading: 'Figtree-SemiBold',
  regular: 'Figtree-Regular',
  medium: 'Figtree-Medium',
  semibold: 'Figtree-SemiBold',
  bold: 'Figtree-Bold',
  mono: 'SplineSansMono-Medium',
};

export function figtreeFamily(weight?: string | number): string {
  const w = String(weight ?? '400');
  if (w === '700' || w === 'bold') return fonts.bold;
  if (w === '600') return fonts.semibold;
  if (w === '500') return fonts.medium;
  return fonts.regular;
}

export const typography = {
  display: { fontFamily: fonts.display, fontSize: 28, letterSpacing: -0.4 },
  h1: { fontFamily: fonts.display, fontSize: 22, letterSpacing: -0.2 },
  h2: { fontFamily: fonts.heading, fontSize: 18, letterSpacing: -0.1 },
  body: { fontFamily: fonts.regular, fontSize: 15 },
  small: { fontFamily: fonts.regular, fontSize: 13 },
  eyebrow: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: 0.8, textTransform: 'uppercase' as const },
};
