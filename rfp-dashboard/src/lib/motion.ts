/** Smooth easing — Material standard, no bounce */
export const smoothEase = [0.4, 0, 0.2, 1] as const;

export const smoothTransition = {
  duration: 0.35,
  ease: smoothEase,
};

export const fastTransition = {
  duration: 0.2,
  ease: smoothEase,
};
