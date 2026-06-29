/** Smooth easing — Material standard, no bounce */
export const smoothEase = [0.4, 0, 0.2, 1] as const;

/** Premium ease-out for auth / hero entrances */
export const expoOutEase = [0.16, 1, 0.3, 1] as const;

export const smoothTransition = {
  duration: 0.35,
  ease: smoothEase,
};

export const fastTransition = {
  duration: 0.2,
  ease: smoothEase,
};

export const authTransition = {
  duration: 0.75,
  ease: expoOutEase,
};

export const authStagger = {
  staggerChildren: 0.11,
  delayChildren: 0.2,
};
